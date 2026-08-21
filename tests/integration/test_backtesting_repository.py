from __future__ import annotations

"""Integration tests for db/repositories/backtesting.py::BacktestScoreRepository
against a real PostgreSQL database.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from investing_agent.schemas.backtesting import BacktestScoreCreate
from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.estimation import EstimateRunCreate, FeatureSnapshotCreate
from investing_agent.schemas.financials import FinancialResultCreate

pytestmark = pytest.mark.integration

CUTOFF = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


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
        CompanyCreate(symbol=f"BEL-{uuid.uuid4().hex[:8]}", name="Bharat Electronics", exchange="NSE")
    )


@pytest.fixture
async def period(db_session, company):
    from investing_agent.db.repositories.financial import FinancialPeriodRepository

    repo = FinancialPeriodRepository(db_session)
    return await repo.get_or_create(company.id, "quarter", 2026, "Q3", date(2025, 12, 31), "Q3FY26")


@pytest.fixture
async def estimate_run(db_session, company, period):
    from investing_agent.db.repositories.estimation import EstimateRunRepository, FeatureSnapshotRepository

    snapshot, _ = await FeatureSnapshotRepository(db_session).get_or_create(
        FeatureSnapshotCreate(
            company_id=company.id, financial_period_id=period.id, cutoff_at=CUTOFF,
            payload={"target_period": {"fiscal_year": 2026}},
        )
    )
    return await EstimateRunRepository(db_session).create(
        EstimateRunCreate(
            company_id=company.id, financial_period_id=period.id, cutoff_at=CUTOFF,
            model_version="deterministic-v1", feature_snapshot_id=snapshot.id,
            revenue_base=Decimal("110.00"), confidence=Decimal("0.60"),
        )
    )


@pytest.fixture
async def financial_result(db_session, company, period):
    from investing_agent.db.repositories.financial import FinancialResultRepository

    row, _ = await FinancialResultRepository(db_session).upsert_versioned(
        FinancialResultCreate(
            period_id=period.id, company_id=company.id, symbol=company.symbol,
            statement_scope="CONSOLIDATED", reporting_basis="QUARTER", is_audited=False,
            result_date=date(2025, 12, 31), currency="INR", unit_scale="CRORE",
            revenue=Decimal("112"), ebitda=None, ebitda_margin_pct=None, ebitda_source=None,
            pbt=None, pat=None, pat_margin_pct=None, eps_basic=None, eps_diluted=None,
            total_debt=None, cash_equivalents=None, operating_cash_flow=None,
            roe_pct=None, roce_pct=None, other_metrics={},
            source_type="test", source_url=None, published_at=datetime.now(UTC),
            data_category="fact", confidence=None, source_document_id=None,
        )
    )
    return row


class TestBacktestScoreRepository:
    async def test_create_and_list_by_company(
        self, db_session, company, period, estimate_run, financial_result
    ) -> None:
        from investing_agent.db.repositories.backtesting import BacktestScoreRepository

        repo = BacktestScoreRepository(db_session)
        created = await repo.create(
            BacktestScoreCreate(
                company_id=company.id, financial_period_id=period.id,
                estimate_run_id=estimate_run.id, financial_result_id=financial_result.id,
                cutoff_at=CUTOFF, model_version="deterministic-v1",
                revenue_error_pct=Decimal("1.8182"), surprise_direction="inline",
                confidence_bucket="medium",
            )
        )
        await db_session.flush()

        rows = await repo.list_by_company(company.id)
        assert len(rows) == 1
        assert rows[0].id == created.id
        assert rows[0].revenue_error_pct == Decimal("1.8182")

    async def test_list_by_model_version_scopes_correctly(
        self, db_session, company, period, estimate_run, financial_result
    ) -> None:
        from investing_agent.db.repositories.backtesting import BacktestScoreRepository

        repo = BacktestScoreRepository(db_session)
        created = await repo.create(
            BacktestScoreCreate(
                company_id=company.id, financial_period_id=period.id,
                estimate_run_id=estimate_run.id, financial_result_id=financial_result.id,
                cutoff_at=CUTOFF, model_version="deterministic-v1",
            )
        )
        await db_session.flush()

        # list_by_model_version is a global (not company-scoped) query, so
        # other tests' committed rows may share this model_version -- assert
        # membership/exclusion of this specific row rather than exact counts.
        v1_ids = {r.id for r in await repo.list_by_model_version("deterministic-v1")}
        v2_ids = {r.id for r in await repo.list_by_model_version("deterministic-v2")}
        assert created.id in v1_ids
        assert created.id not in v2_ids

    async def test_estimate_run_id_is_unique_across_scores(
        self, db_session, company, period, estimate_run, financial_result
    ) -> None:
        from investing_agent.db.repositories.backtesting import BacktestScoreRepository

        repo = BacktestScoreRepository(db_session)
        await repo.create(
            BacktestScoreCreate(
                company_id=company.id, financial_period_id=period.id,
                estimate_run_id=estimate_run.id, financial_result_id=financial_result.id,
                cutoff_at=CUTOFF, model_version="deterministic-v1",
            )
        )
        await db_session.flush()

        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await repo.create(
                BacktestScoreCreate(
                    company_id=company.id, financial_period_id=period.id,
                    estimate_run_id=estimate_run.id, financial_result_id=financial_result.id,
                    cutoff_at=CUTOFF, model_version="deterministic-v2",
                )
            )
