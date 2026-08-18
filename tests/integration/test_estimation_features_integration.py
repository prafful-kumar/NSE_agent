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
from datetime import UTC, date, datetime
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
                source_document_id=None,
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


class TestFeatureSnapshotIdempotency:
    async def test_identical_call_returns_the_same_row(self, db_session, company, target_period) -> None:
        cutoff = datetime.now(UTC)
        first = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        await db_session.flush()
        second = await build_feature_snapshot(db_session, company.id, target_period.id, cutoff)
        await db_session.flush()

        assert first.id == second.id
