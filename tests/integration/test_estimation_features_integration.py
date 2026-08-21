from __future__ import annotations

"""Integration test for services/estimation/features.py::build_feature_snapshot
against a real PostgreSQL database — proves every data source (historical
financials, order book, guidance, segment/operational metrics, active
catalysts/risks, recent news + reviewed interpretations) is correctly
pulled into the payload, that the leakage guard holds, and that snapshot
idempotency works end-to-end.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.company_research import (
    ManagementGuidanceCreate,
    OperationalMetricCreate,
    OrderBookSnapshotCreate,
    SegmentMetricCreate,
)
from investing_agent.schemas.estimation import FeatureSnapshotPayload
from investing_agent.schemas.financials import FinancialResultCreate
from investing_agent.schemas.interpretation import EventInterpretationCreate
from investing_agent.schemas.news import NewsEventCreate
from investing_agent.schemas.research_memory import CatalystCreate, RiskObservationCreate
from investing_agent.schemas.source_documents import SourceDocumentCreate
from investing_agent.services.estimation.features import build_feature_snapshot

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session():
    import os

    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://investing:investing@localhost:5433/investing_agent_test"
    )
    from investing_agent.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def company(db_session):
    from investing_agent.db.repositories.company import CompanyRepository

    repo = CompanyRepository(db_session)
    return await repo.upsert(
        CompanyCreate(
            symbol=f"BEL-{uuid.uuid4().hex[:8]}", name="Bharat Electronics", exchange="NSE"
        )
    )


@pytest.fixture
async def source_document(db_session, company):
    from investing_agent.db.repositories.source_document import SourceDocumentRepository

    repo = SourceDocumentRepository(db_session)
    doc, _ = await repo.get_or_create(
        SourceDocumentCreate(
            company_id=company.id, symbol=company.symbol, exchange="IR",
            filing_type="investor_presentation", document_type="pdf",
            title="Q3FY26 Investor Presentation", content_hash="doc1" * 16,
            source_type="manual_upload", source_url="file:///tmp/ip.pdf",
            data_category="fact",
        )
    )
    return doc


@pytest.fixture
async def target_period(db_session, company):
    from investing_agent.db.repositories.financial import FinancialPeriodRepository

    repo = FinancialPeriodRepository(db_session)
    return await repo.get_or_create(
        company.id, "quarter", 2026, "Q3", date(2025, 12, 31), "Q3FY26"
    )


class TestFullFeatureSnapshotComposition:
    async def test_pulls_every_data_source_into_the_payload(
        self, db_session, company, source_document, target_period
    ) -> None:
        from investing_agent.db.repositories.financial import (
            FinancialPeriodRepository,
            FinancialResultRepository,
        )
        from investing_agent.db.repositories.company_research import (
            ManagementGuidanceRepository,
            OperationalMetricRepository,
            OrderBookSnapshotRepository,
            SegmentMetricRepository,
        )
        from investing_agent.db.repositories.research_memory import (
            CatalystRepository,
            RiskObservationRepository,
        )
        from investing_agent.db.repositories.news import NewsEventRepository
        from investing_agent.db.repositories.interpretation import EventInterpretationRepository

        period_repo = FinancialPeriodRepository(db_session)
        result_repo = FinancialResultRepository(db_session)
        hist_period = await period_repo.get_or_create(
            company.id, "quarter", 2026, "Q2", date(2025, 9, 30), "Q2FY26"
        )
        await result_repo.upsert_versioned(
            FinancialResultCreate(
                period_id=hist_period.id, company_id=company.id, symbol=company.symbol,
                statement_scope="CONSOLIDATED", reporting_basis="QUARTER", is_audited=False,
                result_date=date(2025, 9, 30), currency="INR", unit_scale="CRORE",
                revenue=Decimal("1050"), ebitda=None, ebitda_margin_pct=Decimal("12"),
                ebitda_source=None, pbt=None, pat=Decimal("84"), pat_margin_pct=None,
                eps_basic=None, eps_diluted=Decimal("5"), total_debt=None,
                cash_equivalents=None, operating_cash_flow=None, roe_pct=None, roce_pct=None,
                other_metrics={}, source_type="test", source_url=None,
                published_at=datetime.now(UTC), data_category="fact", confidence=None,
                source_document_id=None, verification_status="verified",
            )
        )

        await OrderBookSnapshotRepository(db_session).create(
            OrderBookSnapshotCreate(
                company_id=company.id, symbol=company.symbol, as_of_date=date(2025, 9, 30),
                order_book_value=Decimal("5000"), currency="INR", unit_scale="CRORE",
                book_to_bill_ratio=Decimal("4.5"), expected_execution_period="18-24 months",
                source_document_id=source_document.id, source_type="manual_upload",
                data_category="fact",
            )
        )
        await ManagementGuidanceRepository(db_session).create(
            ManagementGuidanceCreate(
                company_id=company.id, symbol=company.symbol, fiscal_year=2026,
                guidance_type="revenue", metric_label="Revenue growth",
                guidance_value_text="12-15%", guidance_low=Decimal("12"), guidance_high=Decimal("15"),
                period_label="FY26 full year",
                source_document_id=source_document.id, source_type="manual_upload",
                data_category="fact",
            )
        )
        await SegmentMetricRepository(db_session).create(
            SegmentMetricCreate(
                company_id=company.id, symbol=company.symbol, segment_name="Defence Electronics",
                metric_type="revenue_share", value=Decimal("70"), unit_scale="ACTUAL",
                source_document_id=source_document.id, source_type="manual_upload",
                data_category="fact",
            )
        )
        await OperationalMetricRepository(db_session).create(
            OperationalMetricCreate(
                company_id=company.id, symbol=company.symbol, metric_name="capacity_utilization",
                value=Decimal("85"), unit="%", as_of_date=date(2025, 9, 30),
                source_document_id=source_document.id, source_type="manual_upload",
                data_category="fact",
            )
        )
        await CatalystRepository(db_session).create(
            CatalystCreate(
                company_id=company.id, description="Large export order under negotiation",
                catalyst_type="order_win", status="active",
            )
        )
        await RiskObservationRepository(db_session).create(
            RiskObservationCreate(
                company_id=company.id, description="Execution delay risk on a large order",
                risk_type="operational", severity="medium", status="active",
            )
        )

        news_repo = NewsEventRepository(db_session)
        event = await news_repo.create(
            NewsEventCreate(
                primary_company_id=company.id,
                first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
                representative_headline="BEL wins order worth Rs 500 crore",
            )
        )
        await EventInterpretationRepository(db_session).create(
            EventInterpretationCreate(
                news_event_id=event.id, company_id=company.id,
                impact_classification={"revenue": {"direction": "positive", "magnitude": "medium"}},
                rationale="New order improves near-term revenue visibility.",
                extraction_method="DETERMINISTIC", confidence=Decimal("0.60"),
                review_status="accepted",
            )
        )
        # Backfill reviewed_at directly -- EventInterpretationRepository.create
        # doesn't set it (only mark_accepted does), and this test needs an
        # accepted+reviewed row to prove the review-gate PIT filter.
        from investing_agent.db.models import EventInterpretation
        from sqlalchemy import update

        await db_session.execute(
            update(EventInterpretation)
            .where(EventInterpretation.news_event_id == event.id)
            .values(reviewed_at=datetime.now(UTC), reviewed_by="analyst")
        )
        await db_session.flush()

        cutoff = datetime.now(UTC)
        snapshot_row = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        payload = FeatureSnapshotPayload(**snapshot_row.payload)

        assert len(payload.historical_financials) == 1
        assert payload.historical_financials[0].revenue == Decimal("1050")
        assert payload.verified_history_count == 1
        assert payload.unverified_history_count == 0

        assert payload.order_book is not None
        assert payload.order_book.order_book_value == Decimal("5000")

        assert len(payload.guidance) == 1
        assert payload.guidance[0].guidance_low == Decimal("12")

        assert len(payload.segment_metrics) == 1
        assert payload.segment_metrics[0].segment_name == "Defence Electronics"

        assert len(payload.operational_metrics) == 1
        assert payload.operational_metrics[0].metric_name == "capacity_utilization"

        assert len(payload.active_catalysts) == 1
        assert "export order" in payload.active_catalysts[0].description

        assert len(payload.active_risks) == 1
        assert "Execution delay" in payload.active_risks[0].description

        assert len(payload.recent_news) == 1
        assert payload.recent_news[0].headline == "BEL wins order worth Rs 500 crore"
        assert payload.recent_news[0].impact_classification is not None

    async def test_pending_interpretation_review_is_not_counted_as_a_known_signal(
        self, db_session, company, target_period
    ) -> None:
        from investing_agent.db.repositories.news import NewsEventRepository
        from investing_agent.db.repositories.interpretation import EventInterpretationRepository

        event = await NewsEventRepository(db_session).create(
            NewsEventCreate(
                primary_company_id=company.id,
                first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
                representative_headline="Company hosts investor day",
            )
        )
        await EventInterpretationRepository(db_session).create(
            EventInterpretationCreate(
                news_event_id=event.id, company_id=company.id,
                impact_classification={"revenue": {"direction": "neutral", "magnitude": "low"}},
                rationale="Routine investor engagement.",
                extraction_method="DETERMINISTIC", confidence=Decimal("0.40"),
                review_status="pending",
            )
        )
        await db_session.flush()

        cutoff = datetime.now(UTC)
        snapshot_row = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        payload = FeatureSnapshotPayload(**snapshot_row.payload)

        assert len(payload.recent_news) == 1
        # The headline still appears (it's a known news event as of cutoff)
        # but with no impact_classification, since the interpretation was
        # never reviewed/accepted by cutoff_at.
        assert payload.recent_news[0].impact_classification is None


class TestHistoricalVerificationFiltering:
    """The PIT-diagnosis fix: an NSE structured-API row (verification_status
    default "unverified") must never feed the estimator on its own — only
    verification_status=="verified" rows become HistoricalFinancialPoints.
    See services/estimation/features.py::_build_historical_financials."""

    async def test_unverified_row_is_excluded_but_counted(
        self, db_session, company, target_period
    ) -> None:
        from investing_agent.db.repositories.financial import (
            FinancialPeriodRepository,
            FinancialResultRepository,
        )

        period_repo = FinancialPeriodRepository(db_session)
        result_repo = FinancialResultRepository(db_session)
        hist_period = await period_repo.get_or_create(
            company.id, "quarter", 2026, "Q2", date(2025, 9, 30), "Q2FY26"
        )
        await result_repo.upsert_versioned(
            FinancialResultCreate(
                period_id=hist_period.id, company_id=company.id, symbol=company.symbol,
                statement_scope="CONSOLIDATED", reporting_basis="QUARTER", is_audited=False,
                result_date=date(2025, 9, 30), currency="INR", unit_scale="CRORE",
                revenue=Decimal("1050"), ebitda=None, ebitda_margin_pct=Decimal("12"),
                ebitda_source=None, pbt=None, pat=Decimal("84"), pat_margin_pct=None,
                eps_basic=None, eps_diluted=Decimal("5"), total_debt=None,
                cash_equivalents=None, operating_cash_flow=None, roe_pct=None, roce_pct=None,
                other_metrics={}, source_type="nse_json_hint", source_url=None,
                published_at=datetime.now(UTC), data_category="fact", confidence=None,
                source_document_id=None,
                # verification_status left at its "unverified" default —
                # this is exactly the NSE JSON-hint scenario the fix targets.
            )
        )

        cutoff = datetime.now(UTC)
        snapshot_row = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        payload = FeatureSnapshotPayload(**snapshot_row.payload)

        assert payload.historical_financials == []
        assert payload.verified_history_count == 0
        assert payload.unverified_history_count == 1

    async def test_verified_row_is_included(self, db_session, company, target_period) -> None:
        from investing_agent.db.repositories.financial import (
            FinancialPeriodRepository,
            FinancialResultRepository,
        )

        period_repo = FinancialPeriodRepository(db_session)
        result_repo = FinancialResultRepository(db_session)
        hist_period = await period_repo.get_or_create(
            company.id, "quarter", 2026, "Q2", date(2025, 9, 30), "Q2FY26"
        )
        await result_repo.upsert_versioned(
            FinancialResultCreate(
                period_id=hist_period.id, company_id=company.id, symbol=company.symbol,
                statement_scope="CONSOLIDATED", reporting_basis="QUARTER", is_audited=False,
                result_date=date(2025, 9, 30), currency="INR", unit_scale="CRORE",
                revenue=Decimal("1050"), ebitda=None, ebitda_margin_pct=Decimal("12"),
                ebitda_source=None, pbt=None, pat=Decimal("84"), pat_margin_pct=None,
                eps_basic=None, eps_diluted=Decimal("5"), total_debt=None,
                cash_equivalents=None, operating_cash_flow=None, roe_pct=None, roce_pct=None,
                other_metrics={}, source_type="nse_filing_pdf", source_url=None,
                published_at=datetime.now(UTC), data_category="fact", confidence=None,
                source_document_id=None, verification_status="verified",
            )
        )

        cutoff = datetime.now(UTC)
        snapshot_row = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        payload = FeatureSnapshotPayload(**snapshot_row.payload)

        assert len(payload.historical_financials) == 1
        assert payload.verified_history_count == 1
        assert payload.unverified_history_count == 0


class TestFeatureSnapshotIdempotency:
    async def test_identical_call_returns_the_same_row(self, db_session, company, target_period) -> None:
        cutoff = datetime.now(UTC)
        first = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        await db_session.flush()
        second = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        await db_session.flush()

        assert first.id == second.id


class TestHistoricalAvailableAtIsNotIngestionTime:
    """PIT regression test for the exact bug the user diagnosed from a real
    BEL backtest run: a Q3FY25 EstimateRun had cutoff_at in August 2026 —
    proof that available_at was defaulting to ingestion time rather than
    reflecting when the actual result was historically published.

    Drives the real, non-bypassed path an NSE JSON-hint row takes: a
    RawFinancialResult carrying only a result_date (no intraday
    published_at, exactly like NSE's structured API) ->
    FinancialResultNormalizer.normalize() (derives available_at from the
    historical result_date, DATE_ONLY precision, per
    _derive_provenance_timestamps) -> FinancialResultRepository
    .upsert_versioned() (persists it) ->
    FinancialResultRepository.get_earliest_available_at() (what
    services/backtesting/runner.py actually calls to derive cutoff_at).

    The result published/filed in Jan 2025 must yield a historical
    (Jan-2025) cutoff -- it must never become "now" (August 2026, when this
    test happens to run / when a real ingestion run would have executed).
    """

    async def test_q3fy25_result_filed_jan_2025_yields_a_2025_cutoff_not_the_ingestion_year(
        self, db_session, company
    ) -> None:
        from investing_agent.db.repositories.financial import (
            FinancialPeriodRepository,
            FinancialResultRepository,
        )
        from investing_agent.services.normalization import FinancialResultNormalizer
        from investing_agent.services.sources.interfaces import RawFinancialResult

        period_repo = FinancialPeriodRepository(db_session)
        result_repo = FinancialResultRepository(db_session)
        period = await period_repo.get_or_create(
            company.id, "quarter", 2025, "Q3", date(2024, 12, 31), "Q3FY25"
        )

        raw = RawFinancialResult(
            period_start="01-Oct-2024",
            period_end="31-Dec-2024",
            result_date="30-Jan-2025",
            revenue="5200",
            pat="420",
            pbt="560",
            eps_basic="1.15",
            is_audited_hint=False,
            statement_scope_hint="CONSOLIDATED",
            unit_scale_hint="CRORE",
            extraction_method="structured_api",
            raw={},
        )

        data = FinancialResultNormalizer().normalize(
            raw,
            company_id=company.id,
            period_id=period.id,
            symbol=company.symbol,
            source_type="nse_json_hint",
            source_url=None,
            # The real NSE JSON-hint ingestion path has no intraday
            # publication timestamp -- only result_date. published_at=None
            # here is what forces the DATE_ONLY derivation path.
            published_at=None,
            source_document_id=None,
        )
        assert data.available_at is not None
        assert data.available_at.year == 2025
        assert data.available_at.month == 1
        assert data.timestamp_precision == "DATE_ONLY"

        await result_repo.upsert_versioned(data)
        await db_session.flush()

        earliest_available_at = await result_repo.get_earliest_available_at(period.id)

        assert earliest_available_at is not None
        assert earliest_available_at.year == 2025
        assert earliest_available_at.month == 1
        # This is the exact invariant the user's diagnosis demands: the
        # ingestion year (whenever this sync actually runs -- e.g. 2026)
        # must never leak into the historical cutoff derivation.
        assert earliest_available_at.year != datetime.now(UTC).year

        cutoff_at = earliest_available_at - timedelta(seconds=1)
        assert cutoff_at.year == 2025


async def _upsert_verified_result(
    db_session,
    *,
    company,
    period,
    available_at: datetime,
    revenue: Decimal = Decimal("1000"),
    source_type: str = "nse_filing_pdf",
):
    from investing_agent.db.repositories.financial import FinancialResultRepository

    result_repo = FinancialResultRepository(db_session)
    row, _ = await result_repo.upsert_versioned(
        FinancialResultCreate(
            period_id=period.id, company_id=company.id, symbol=company.symbol,
            statement_scope="CONSOLIDATED", reporting_basis="QUARTER", is_audited=True,
            result_date=period.period_end, currency="INR", unit_scale="CRORE",
            revenue=revenue, ebitda=None, ebitda_margin_pct=Decimal("12"),
            ebitda_source=None, pbt=None, pat=Decimal("84"), pat_margin_pct=None,
            eps_basic=None, eps_diluted=Decimal("5"), total_debt=None,
            cash_equivalents=None, operating_cash_flow=None, roe_pct=None, roce_pct=None,
            other_metrics={}, source_type=source_type, source_url=None,
            published_at=available_at, available_at=available_at,
            data_category="fact", confidence=None, source_document_id=None,
            verification_status="verified",
        )
    )
    await db_session.flush()
    return row


class TestFeatureSnapshotCacheInvalidation:
    """Regression coverage for the real BEL staleness bug: build_feature_snapshot
    used to be content-addressed only by (company_id, financial_period_id,
    cutoff_at), so a snapshot cached before a historical quarter was
    transcribed would be served forever after, even once the newly-verified
    FinancialResult became visible at that same cutoff_at. See
    services/estimation/features.py::_fingerprint and
    db/repositories/estimation.py::FeatureSnapshotRepository.get_matching."""

    async def test_new_older_verified_result_invalidates_cache_and_rebuilds(
        self, db_session, company, target_period
    ) -> None:
        from investing_agent.db.repositories.financial import FinancialPeriodRepository

        period_repo = FinancialPeriodRepository(db_session)
        q2 = await period_repo.get_or_create(
            company.id, "quarter", 2026, "Q2", date(2025, 9, 30), "Q2FY26"
        )
        await _upsert_verified_result(
            db_session, company=company, period=q2,
            available_at=datetime(2025, 10, 15, tzinfo=UTC),
        )

        cutoff = datetime(2025, 11, 1, tzinfo=UTC)
        first = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        await db_session.flush()
        first_payload = FeatureSnapshotPayload(**first.payload)
        assert first_payload.verified_history_count == 1

        # A quarter earlier than Q2FY26, whose available_at nonetheless
        # predates the cutoff -- exactly the "newly-transcribed older
        # history became visible at an already-cached cutoff" scenario.
        q1 = await period_repo.get_or_create(
            company.id, "quarter", 2026, "Q1", date(2025, 6, 30), "Q1FY26"
        )
        await _upsert_verified_result(
            db_session, company=company, period=q1,
            available_at=datetime(2025, 7, 15, tzinfo=UTC),
        )

        second = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        await db_session.flush()
        second_payload = FeatureSnapshotPayload(**second.payload)

        assert second.id != first.id
        assert second_payload.verified_history_count == 2
        assert second.input_fingerprint != first.input_fingerprint

    async def test_future_available_at_result_does_not_invalidate_cache(
        self, db_session, company, target_period
    ) -> None:
        from investing_agent.db.repositories.financial import FinancialPeriodRepository

        period_repo = FinancialPeriodRepository(db_session)
        q2 = await period_repo.get_or_create(
            company.id, "quarter", 2026, "Q2", date(2025, 9, 30), "Q2FY26"
        )
        await _upsert_verified_result(
            db_session, company=company, period=q2,
            available_at=datetime(2025, 10, 15, tzinfo=UTC),
        )

        cutoff = datetime(2025, 11, 1, tzinfo=UTC)
        first = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        await db_session.flush()

        q1 = await period_repo.get_or_create(
            company.id, "quarter", 2026, "Q1", date(2025, 6, 30), "Q1FY26"
        )
        # available_at is AFTER cutoff -- not yet visible as of cutoff, so
        # it must not have been part of the PIT query and must not affect
        # the fingerprint.
        await _upsert_verified_result(
            db_session, company=company, period=q1,
            available_at=datetime(2025, 12, 1, tzinfo=UTC),
        )

        second = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        await db_session.flush()

        assert second.id == first.id

    async def test_identical_reingestion_does_not_invalidate_cache(
        self, db_session, company, target_period
    ) -> None:
        from investing_agent.db.repositories.financial import FinancialPeriodRepository

        period_repo = FinancialPeriodRepository(db_session)
        q2 = await period_repo.get_or_create(
            company.id, "quarter", 2026, "Q2", date(2025, 9, 30), "Q2FY26"
        )
        available_at = datetime(2025, 10, 15, tzinfo=UTC)
        await _upsert_verified_result(db_session, company=company, period=q2, available_at=available_at)

        cutoff = datetime(2025, 11, 1, tzinfo=UTC)
        first = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        await db_session.flush()

        # Re-ingesting the exact same reported values (same period/scope/
        # basis/source_type/available_at) hits upsert_versioned's
        # _values_equal branch -- same row, no version bump -- so the
        # fingerprint must be unchanged and the cache reused.
        await _upsert_verified_result(db_session, company=company, period=q2, available_at=available_at)

        second = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        await db_session.flush()

        assert second.id == first.id

    async def test_revised_qualifying_result_invalidates_cache(
        self, db_session, company, target_period
    ) -> None:
        from investing_agent.db.repositories.financial import FinancialPeriodRepository

        period_repo = FinancialPeriodRepository(db_session)
        q2 = await period_repo.get_or_create(
            company.id, "quarter", 2026, "Q2", date(2025, 9, 30), "Q2FY26"
        )
        available_at = datetime(2025, 10, 15, tzinfo=UTC)
        await _upsert_verified_result(
            db_session, company=company, period=q2, available_at=available_at, revenue=Decimal("1000")
        )

        cutoff = datetime(2025, 11, 1, tzinfo=UTC)
        first = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        await db_session.flush()

        # A genuine restatement -- same period/scope/basis/source_type but a
        # different reported value -- creates a brand-new versioned row
        # (upsert_versioned's version+1 branch), which must invalidate.
        await _upsert_verified_result(
            db_session, company=company, period=q2, available_at=available_at, revenue=Decimal("1100")
        )

        second = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        await db_session.flush()
        second_payload = FeatureSnapshotPayload(**second.payload)

        assert second.id != first.id
        assert second.input_fingerprint != first.input_fingerprint
        assert second_payload.historical_financials[0].revenue == Decimal("1100")

    async def test_old_estimate_run_still_references_its_original_immutable_snapshot(
        self, db_session, company, target_period
    ) -> None:
        from investing_agent.db.models import FeatureSnapshot
        from investing_agent.db.repositories.estimation import EstimateRunRepository
        from investing_agent.db.repositories.financial import FinancialPeriodRepository
        from investing_agent.schemas.estimation import EstimateRunCreate

        period_repo = FinancialPeriodRepository(db_session)
        q2 = await period_repo.get_or_create(
            company.id, "quarter", 2026, "Q2", date(2025, 9, 30), "Q2FY26"
        )
        await _upsert_verified_result(
            db_session, company=company, period=q2,
            available_at=datetime(2025, 10, 15, tzinfo=UTC),
        )

        cutoff = datetime(2025, 11, 1, tzinfo=UTC)
        first = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        await db_session.flush()
        first_payload = FeatureSnapshotPayload(**first.payload)

        estimate_run = await EstimateRunRepository(db_session).create(
            EstimateRunCreate(
                company_id=company.id, financial_period_id=target_period.id, cutoff_at=cutoff,
                model_version="deterministic-v1", feature_snapshot_id=first.id,
                revenue_base=Decimal("110.00"), confidence=Decimal("0.60"),
            )
        )
        await db_session.flush()

        q1 = await period_repo.get_or_create(
            company.id, "quarter", 2026, "Q1", date(2025, 6, 30), "Q1FY26"
        )
        await _upsert_verified_result(
            db_session, company=company, period=q1,
            available_at=datetime(2025, 7, 15, tzinfo=UTC),
        )

        second = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        await db_session.flush()
        assert second.id != first.id

        # The old EstimateRun's FK is untouched, and the row it points at
        # still holds its original payload -- never mutated in place.
        assert estimate_run.feature_snapshot_id == first.id
        original = await db_session.get(FeatureSnapshot, first.id)
        assert original is not None
        original_payload = FeatureSnapshotPayload(**original.payload)
        assert original_payload.verified_history_count == first_payload.verified_history_count == 1
