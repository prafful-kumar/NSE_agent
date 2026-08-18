from __future__ import annotations

"""run_backtest: for every historical quarter that already has an actual
FinancialResult, re-run EstimationService.generate_estimate with a cutoff_at
derived from real data (never a guessed offset) and score the result.

cutoff_at = earliest available_at across every FinancialResult row tied to
the target period, minus one second (see
FinancialResultRepository.get_earliest_available_at). This guarantees the
FeatureSnapshot built for the backtest cannot see any data that wasn't in
the DB before the real results were — the same PIT leakage guard Phase 5A
already enforces inside build_feature_snapshot, applied here at the cutoff
selection level too.

Reuses EstimationService.generate_estimate verbatim (the same code path a
live estimate takes) — this is what makes a backtest a genuine test of the
estimator rather than a separate, possibly-diverging implementation.
"""

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import BacktestScore, FinancialPeriod, FinancialResult
from investing_agent.db.repositories.backtesting import BacktestScoreRepository
from investing_agent.db.repositories.company import CompanyRepository
from investing_agent.db.repositories.estimation import FeatureSnapshotRepository
from investing_agent.db.repositories.financial import FinancialResultRepository
from investing_agent.schemas.backtesting import BacktestDiagnostic
from investing_agent.schemas.estimation import EstimateRunRead, FeatureSnapshotPayload
from investing_agent.schemas.financials import FinancialResultRead
from investing_agent.services.backtesting.scoring import score_estimate
from investing_agent.services.estimation.narrator import EstimateNarrator
from investing_agent.services.estimation.service import EstimationService

_SCOPE_PREFERENCE = ("CONSOLIDATED", "STANDALONE", "UNRESOLVED")


def _pick_preferred_scope(rows: list[FinancialResult]) -> FinancialResult | None:
    by_scope = {r.statement_scope: r for r in rows}
    for scope in _SCOPE_PREFERENCE:
        if scope in by_scope:
            return by_scope[scope]
    return None


@dataclass
class BacktestRunResult:
    scores: list[BacktestScore] = field(default_factory=list)
    diagnostics: list[BacktestDiagnostic] = field(default_factory=list)


async def run_backtest(
    session: AsyncSession,
    *,
    company_id: uuid.UUID | None = None,
    model_version: str = "deterministic-v1",
    narrator: EstimateNarrator | None = None,
) -> BacktestRunResult:
    company_repo = CompanyRepository(session)
    result_repo = FinancialResultRepository(session)
    snapshot_repo = FeatureSnapshotRepository(session)
    score_repo = BacktestScoreRepository(session)
    service = EstimationService(session, narrator=narrator)

    if company_id is not None:
        company = await company_repo.get(company_id)
        companies = [company] if company is not None else []
    else:
        companies = await company_repo.list()

    result = BacktestRunResult()

    for company in companies:
        results = await result_repo.list_by_company(company.id)
        quarterly = [r for r in results if r.reporting_basis == "QUARTER"]

        by_period: dict[uuid.UUID, list[FinancialResult]] = {}
        for r in quarterly:
            by_period.setdefault(r.period_id, []).append(r)

        for period_id, rows in by_period.items():
            actual_row = _pick_preferred_scope(rows)
            if actual_row is None:
                continue

            period = await session.get(FinancialPeriod, period_id)
            period_label = period.label if period is not None else str(period_id)

            earliest_available_at = await result_repo.get_earliest_available_at(period_id)
            if earliest_available_at is None:
                # No actual has a known available_at at all yet — nothing to
                # derive a PIT-safe cutoff from. Skip entirely (no score, no
                # misleading "attempted" row) rather than guess an offset.
                result.diagnostics.append(
                    BacktestDiagnostic(
                        period_id=period_id, period_label=period_label,
                        actual_available_at=None, actual_ingested_at=actual_row.ingested_at,
                        cutoff_at=None, verified_history_count=0, unverified_history_count=0,
                        estimate_status="SKIPPED", reason="NO_ACTUAL_AVAILABLE_AT",
                    )
                )
                continue
            cutoff_at = earliest_available_at - timedelta(seconds=1)

            run = await service.generate_estimate(
                company_id=company.id,
                financial_period_id=period_id,
                cutoff_at=cutoff_at,
                model_version=model_version,
            )

            snapshot_row = await snapshot_repo.get(run.feature_snapshot_id)
            payload = FeatureSnapshotPayload(**snapshot_row.payload)
            baseline_pat = (
                payload.historical_financials[-1].pat if payload.historical_financials else None
            )

            verified_n = payload.verified_history_count
            unverified_n = payload.unverified_history_count
            if verified_n == 0 and unverified_n == 0:
                status, reason = "UNSCORABLE", "NO_HISTORY_AVAILABLE"
            elif verified_n == 0:
                status, reason = "UNSCORABLE", "NO_VERIFIED_HISTORY"
            else:
                status, reason = "SCORED", None

            score_data = score_estimate(
                run=EstimateRunRead.model_validate(run),
                actual=FinancialResultRead.model_validate(actual_row),
                baseline_pat=baseline_pat,
            )
            score_data = score_data.model_copy(
                update={
                    "status": status,
                    "unscorable_reason": reason,
                    "verified_history_count": verified_n,
                    "unverified_history_count": unverified_n,
                }
            )
            score = await score_repo.create(score_data)
            result.scores.append(score)
            result.diagnostics.append(
                BacktestDiagnostic(
                    period_id=period_id, period_label=period_label,
                    actual_available_at=actual_row.available_at,
                    actual_ingested_at=actual_row.ingested_at,
                    cutoff_at=cutoff_at,
                    verified_history_count=verified_n, unverified_history_count=unverified_n,
                    estimate_status=status, reason=reason,
                )
            )

    return result
