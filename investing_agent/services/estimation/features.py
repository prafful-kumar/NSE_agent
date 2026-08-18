from __future__ import annotations

"""Builds the frozen FeatureSnapshot an estimate is derived from.

Every query here is point-in-time filtered to cutoff_at — this module is
the single place responsible for preventing look-ahead bias, so the
leakage guard below is load-bearing: a historical FinancialResult is only
included if its *period* ends strictly before the target period AND its
value was available_at <= cutoff_at. A row can pass the available_at check
and still be excluded because it belongs to the target period itself or a
later one (this matters when a target period's own result rows already
exist in the DB from a prior sync, e.g. when generating a backtest
estimate for a quarter whose actuals have since been ingested).

v1 supports quarterly targets only — historical comparables are restricted
to reporting_basis="QUARTER" rows, so an annual target period will
correctly end up with a sparse/empty history and a low-confidence,
mostly-None estimate rather than a fabricated one.
"""

import uuid
from datetime import datetime

from sqlalchemy import select

from investing_agent.db.models import Catalyst, FeatureSnapshot, FinancialPeriod, RiskObservation
from investing_agent.db.repositories.company_research import (
    ManagementGuidanceRepository,
    OperationalMetricRepository,
    OrderBookSnapshotRepository,
    SegmentMetricRepository,
)
from investing_agent.db.repositories.estimation import FeatureSnapshotRepository
from investing_agent.db.repositories.financial import FinancialResultRepository
from investing_agent.db.repositories.interpretation import EventInterpretationRepository
from investing_agent.db.repositories.news import NewsEventRepository
from investing_agent.schemas.estimation import (
    CatalystPoint,
    FeatureSnapshotCreate,
    FeatureSnapshotPayload,
    GuidancePoint,
    HistoricalFinancialPoint,
    NewsSignalPoint,
    OperationalMetricPoint,
    OrderBookPoint,
    RiskPoint,
    SegmentMetricPoint,
    TargetPeriodInfo,
)

_MAX_RECENT_NEWS = 20
_SCOPE_PREFERENCE = ("CONSOLIDATED", "STANDALONE", "UNRESOLVED")


async def build_feature_snapshot(
    session, company_id: uuid.UUID, financial_period_id: uuid.UUID, cutoff_at: datetime
) -> FeatureSnapshot:
    snapshot_repo = FeatureSnapshotRepository(session)
    existing = await snapshot_repo.get_by_key(company_id, financial_period_id, cutoff_at)
    if existing is not None:
        return existing

    target_period = await session.get(FinancialPeriod, financial_period_id)
    if target_period is None:
        raise ValueError(f"financial_period_id {financial_period_id} not found")

    target_period_info = TargetPeriodInfo(
        fiscal_year=target_period.fiscal_year,
        quarter=target_period.quarter,
        period_type=target_period.period_type,
        period_end=target_period.period_end,
        label=target_period.label,
    )

    (
        historical_financials,
        used_unresolved_scope,
        verified_history_count,
        unverified_history_count,
    ) = await _build_historical_financials(session, company_id, target_period, cutoff_at)
    order_book = await _build_order_book(session, company_id, cutoff_at)
    guidance = await _build_guidance(session, company_id, target_period.fiscal_year, cutoff_at)
    segment_metrics = await _build_segment_metrics(session, company_id, cutoff_at)
    operational_metrics = await _build_operational_metrics(session, company_id, cutoff_at)
    active_catalysts = await _build_active_catalysts(session, company_id, cutoff_at)
    active_risks = await _build_active_risks(session, company_id, cutoff_at)
    recent_news = await _build_recent_news(session, company_id, cutoff_at)

    payload = FeatureSnapshotPayload(
        target_period=target_period_info,
        historical_financials=historical_financials,
        order_book=order_book,
        guidance=guidance,
        segment_metrics=segment_metrics,
        operational_metrics=operational_metrics,
        active_catalysts=active_catalysts,
        active_risks=active_risks,
        recent_news=recent_news,
        used_unresolved_scope=used_unresolved_scope,
        verified_history_count=verified_history_count,
        unverified_history_count=unverified_history_count,
    )

    return await snapshot_repo.get_or_create(
        FeatureSnapshotCreate(
            company_id=company_id,
            financial_period_id=financial_period_id,
            cutoff_at=cutoff_at,
            payload=payload.model_dump(mode="json"),
        )
    )


async def _build_historical_financials(
    session, company_id: uuid.UUID, target_period: FinancialPeriod, cutoff_at: datetime
) -> tuple[list[HistoricalFinancialPoint], bool, int, int]:
    """Returns (points, used_unresolved_scope, verified_history_count,
    unverified_history_count).

    Only verification_status=="verified" rows ever become a
    HistoricalFinancialPoint — an unreconciled NSE JSON-hint row
    (source_type="nse_json_hint") is not trustworthy enough to feed the
    estimator on its own (see the empirically-documented BEL
    re_con_pro_loss scope mislabeling: an API value that "looks" like a
    fact can still be wrong). verified_history_count/unverified_history_count
    are computed over every non-leaked candidate row (any scope), for
    diagnostics — see run_backtest's UNSCORABLE/NO_VERIFIED_HISTORY marking.
    """
    result_repo = FinancialResultRepository(session)
    results = await result_repo.list_by_company_as_of(company_id, cutoff_at)

    period_ids = {r.period_id for r in results}
    periods_by_id: dict[uuid.UUID, FinancialPeriod] = {}
    if period_ids:
        rows = await session.execute(
            select(FinancialPeriod).where(FinancialPeriod.id.in_(period_ids))
        )
        periods_by_id = {p.id: p for p in rows.scalars().all()}

    by_period: dict[uuid.UUID, list] = {}
    for r in results:
        if r.reporting_basis != "QUARTER":
            continue
        period = periods_by_id.get(r.period_id)
        if period is None:
            continue
        # Leakage guard: never include the target period itself or anything
        # ending on/after it, even if available_at <= cutoff_at.
        if period.id == target_period.id or period.period_end >= target_period.period_end:
            continue
        by_period.setdefault(r.period_id, []).append((period, r))

    verified_history_count = 0
    unverified_history_count = 0
    for rows in by_period.values():
        for _period, r in rows:
            if r.verification_status == "verified":
                verified_history_count += 1
            else:
                unverified_history_count += 1

    points: list[HistoricalFinancialPoint] = []
    used_unresolved_scope = False
    for period_id, rows in by_period.items():
        verified_rows = [(period, r) for period, r in rows if r.verification_status == "verified"]
        rows_by_scope = {r.statement_scope: (period, r) for period, r in verified_rows}
        for scope in _SCOPE_PREFERENCE:
            if scope in rows_by_scope:
                period, r = rows_by_scope[scope]
                if scope == "UNRESOLVED":
                    used_unresolved_scope = True
                points.append(
                    HistoricalFinancialPoint(
                        fiscal_year=period.fiscal_year,
                        quarter=period.quarter,
                        period_type=period.period_type,
                        period_end=period.period_end,
                        statement_scope=scope,
                        revenue=r.revenue,
                        ebitda_margin_pct=r.ebitda_margin_pct,
                        pat=r.pat,
                        eps_diluted=r.eps_diluted,
                    )
                )
                break

    points.sort(key=lambda p: p.period_end)
    return points, used_unresolved_scope, verified_history_count, unverified_history_count


async def _build_order_book(session, company_id: uuid.UUID, cutoff_at: datetime) -> OrderBookPoint | None:
    rows = await OrderBookSnapshotRepository(session).list_by_company_as_of(company_id, cutoff_at)
    if not rows:
        return None
    latest = rows[0]
    return OrderBookPoint(
        as_of_date=latest.as_of_date,
        order_book_value=latest.order_book_value,
        currency=latest.currency,
        unit_scale=latest.unit_scale,
        book_to_bill_ratio=latest.book_to_bill_ratio,
        expected_execution_period=latest.expected_execution_period,
    )


async def _build_guidance(
    session, company_id: uuid.UUID, target_fiscal_year: int, cutoff_at: datetime
) -> list[GuidancePoint]:
    rows = await ManagementGuidanceRepository(session).list_by_company_as_of(company_id, cutoff_at)
    return [
        GuidancePoint(
            fiscal_year=g.fiscal_year,
            guidance_type=g.guidance_type,
            metric_label=g.metric_label,
            guidance_value_text=g.guidance_value_text,
            guidance_low=g.guidance_low,
            guidance_high=g.guidance_high,
            period_label=g.period_label,
        )
        for g in rows
        if g.fiscal_year == target_fiscal_year
    ]


async def _build_segment_metrics(session, company_id: uuid.UUID, cutoff_at: datetime) -> list[SegmentMetricPoint]:
    rows = await SegmentMetricRepository(session).list_by_company_as_of(company_id, cutoff_at)
    return [
        SegmentMetricPoint(
            segment_name=m.segment_name, metric_type=m.metric_type,
            value=m.value, unit_scale=m.unit_scale,
        )
        for m in rows
    ]


async def _build_operational_metrics(
    session, company_id: uuid.UUID, cutoff_at: datetime
) -> list[OperationalMetricPoint]:
    rows = await OperationalMetricRepository(session).list_by_company_as_of(company_id, cutoff_at)
    return [
        OperationalMetricPoint(
            metric_name=m.metric_name, value=m.value, unit=m.unit, as_of_date=m.as_of_date,
        )
        for m in rows
    ]


async def _build_active_catalysts(session, company_id: uuid.UUID, cutoff_at: datetime) -> list[CatalystPoint]:
    rows = await session.execute(
        select(Catalyst).where(
            Catalyst.company_id == company_id,
            Catalyst.status == "active",
            Catalyst.created_at <= cutoff_at,
        )
    )
    return [
        CatalystPoint(description=c.description, catalyst_type=c.catalyst_type, expected_by=c.expected_by)
        for c in rows.scalars().all()
    ]


async def _build_active_risks(session, company_id: uuid.UUID, cutoff_at: datetime) -> list[RiskPoint]:
    rows = await session.execute(
        select(RiskObservation).where(
            RiskObservation.company_id == company_id,
            RiskObservation.status == "active",
            RiskObservation.created_at <= cutoff_at,
        )
    )
    return [
        RiskPoint(description=r.description, risk_type=r.risk_type, severity=r.severity)
        for r in rows.scalars().all()
    ]


async def _build_recent_news(session, company_id: uuid.UUID, cutoff_at: datetime) -> list[NewsSignalPoint]:
    events = await NewsEventRepository(session).list_by_company_as_of(company_id, cutoff_at)
    interp_repo = EventInterpretationRepository(session)

    points: list[NewsSignalPoint] = []
    for event in events[:_MAX_RECENT_NEWS]:
        interpretations = await interp_repo.list_by_event_and_company(event.id, company_id)
        # Only an accepted interpretation whose review itself happened by
        # cutoff_at counts as a signal — a pending/future-reviewed
        # interpretation is not yet a known fact as of cutoff_at.
        accepted = [
            i for i in interpretations
            if i.review_status == "accepted"
            and i.reviewed_at is not None
            and i.reviewed_at <= cutoff_at
        ]
        if accepted:
            i = accepted[0]
            points.append(
                NewsSignalPoint(
                    headline=event.representative_headline,
                    first_seen_at=event.first_seen_at,
                    impact_classification=i.impact_classification,
                    rationale=i.rationale,
                )
            )
        else:
            points.append(
                NewsSignalPoint(headline=event.representative_headline, first_seen_at=event.first_seen_at)
            )
    return points
