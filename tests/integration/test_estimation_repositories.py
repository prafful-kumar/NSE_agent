from __future__ import annotations

"""Integration tests for db/repositories/estimation.py against a real
PostgreSQL database — proves FeatureSnapshot content-addressed idempotency
and EstimateRun persistence/listing.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.estimation import EstimateRunCreate, FeatureSnapshotCreate

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
        CompanyCreate(
            symbol=f"BEL-{uuid.uuid4().hex[:8]}", name="Bharat Electronics", exchange="NSE"
        )
    )


@pytest.fixture
async def period(db_session, company):
    from investing_agent.db.repositories.financial import FinancialPeriodRepository

    repo = FinancialPeriodRepository(db_session)
    return await repo.get_or_create(
        company.id, "quarter", 2026, "Q3", date(2025, 12, 31), "Q3FY26"
    )


class TestFeatureSnapshotRepository:
    async def test_get_or_create_is_idempotent_for_identical_key(self, db_session, company, period) -> None:
        from investing_agent.db.repositories.estimation import FeatureSnapshotRepository

        repo = FeatureSnapshotRepository(db_session)
        data = FeatureSnapshotCreate(
            company_id=company.id, financial_period_id=period.id, cutoff_at=CUTOFF,
            payload={"target_period": {"fiscal_year": 2026}},
        )
        first = await repo.get_or_create(data)
        await db_session.flush()
        second = await repo.get_or_create(data)
        await db_session.flush()

        assert first.id == second.id

    async def test_different_cutoff_creates_a_new_row(self, db_session, company, period) -> None:
        from investing_agent.db.repositories.estimation import FeatureSnapshotRepository

        repo = FeatureSnapshotRepository(db_session)
        first = await repo.get_or_create(
            FeatureSnapshotCreate(
                company_id=company.id, financial_period_id=period.id, cutoff_at=CUTOFF,
                payload={"target_period": {"fiscal_year": 2026}},
            )
        )
        await db_session.flush()
        second = await repo.get_or_create(
            FeatureSnapshotCreate(
                company_id=company.id, financial_period_id=period.id,
                cutoff_at=CUTOFF.replace(hour=13),
                payload={"target_period": {"fiscal_year": 2026}},
            )
        )
        await db_session.flush()

        assert first.id != second.id

    async def test_get_by_key_returns_none_when_absent(self, db_session, company, period) -> None:
        from investing_agent.db.repositories.estimation import FeatureSnapshotRepository

        repo = FeatureSnapshotRepository(db_session)
        result = await repo.get_by_key(company.id, period.id, CUTOFF)
        assert result is None


class TestEstimateRunRepository:
    async def test_create_and_list_by_company(self, db_session, company, period) -> None:
        from investing_agent.db.repositories.estimation import (
            EstimateRunRepository,
            FeatureSnapshotRepository,
        )

        snapshot = await FeatureSnapshotRepository(db_session).get_or_create(
            FeatureSnapshotCreate(
                company_id=company.id, financial_period_id=period.id, cutoff_at=CUTOFF,
                payload={"target_period": {"fiscal_year": 2026}},
            )
        )
        await db_session.flush()

        run_repo = EstimateRunRepository(db_session)
        created = await run_repo.create(
            EstimateRunCreate(
                company_id=company.id, financial_period_id=period.id, cutoff_at=CUTOFF,
                model_version="deterministic-v1", feature_snapshot_id=snapshot.id,
                revenue_base=Decimal("110.00"), confidence=Decimal("0.60"),
            )
        )
        await db_session.flush()

        rows = await run_repo.list_by_company(company.id)
        assert len(rows) == 1
        assert rows[0].id == created.id
        assert rows[0].revenue_base == Decimal("110.00")

    async def test_list_by_period_scopes_to_a_single_period(self, db_session, company, period) -> None:
        from investing_agent.db.repositories.estimation import (
            EstimateRunRepository,
            FeatureSnapshotRepository,
        )
        from investing_agent.db.repositories.financial import FinancialPeriodRepository

        other_period = await FinancialPeriodRepository(db_session).get_or_create(
            company.id, "quarter", 2026, "Q4", date(2026, 3, 31), "Q4FY26"
        )

        snapshot_repo = FeatureSnapshotRepository(db_session)
        snap1 = await snapshot_repo.get_or_create(
            FeatureSnapshotCreate(
                company_id=company.id, financial_period_id=period.id, cutoff_at=CUTOFF,
                payload={"target_period": {"fiscal_year": 2026}},
            )
        )
        snap2 = await snapshot_repo.get_or_create(
            FeatureSnapshotCreate(
                company_id=company.id, financial_period_id=other_period.id, cutoff_at=CUTOFF,
                payload={"target_period": {"fiscal_year": 2026}},
            )
        )
        await db_session.flush()

        run_repo = EstimateRunRepository(db_session)
        await run_repo.create(
            EstimateRunCreate(
                company_id=company.id, financial_period_id=period.id, cutoff_at=CUTOFF,
                model_version="deterministic-v1", feature_snapshot_id=snap1.id,
            )
        )
        await run_repo.create(
            EstimateRunCreate(
                company_id=company.id, financial_period_id=other_period.id, cutoff_at=CUTOFF,
                model_version="deterministic-v1", feature_snapshot_id=snap2.id,
            )
        )
        await db_session.flush()

        rows = await run_repo.list_by_period(period.id)
        assert len(rows) == 1
        assert rows[0].financial_period_id == period.id
