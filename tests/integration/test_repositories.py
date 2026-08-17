from __future__ import annotations
"""Integration tests for repositories.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration

To start the test DB: docker-compose up postgres_test -d
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session():
    """Create a test database session.

    Requires postgres_test container to be running.
    Rolls back all changes after each test.
    """
    import os
    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://investing:investing@localhost:5433/investing_agent_test"
    )
    from investing_agent.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


class TestCompanyRepository:
    async def test_upsert_creates_company(self, db_session):
        from investing_agent.db.repositories.company import CompanyRepository
        from investing_agent.schemas.company import CompanyCreate
        repo = CompanyRepository(db_session)
        company = await repo.upsert(
            CompanyCreate(symbol="TESTCO", name="Test Company Ltd", exchange="NSE")
        )
        assert company.id is not None
        assert company.symbol == "TESTCO"

    async def test_upsert_updates_existing(self, db_session):
        from investing_agent.db.repositories.company import CompanyRepository
        from investing_agent.schemas.company import CompanyCreate
        repo = CompanyRepository(db_session)
        await repo.upsert(CompanyCreate(symbol="TESTCO2", name="Old Name", exchange="NSE"))
        updated = await repo.upsert(CompanyCreate(symbol="TESTCO2", name="New Name", exchange="NSE"))
        assert updated.name == "New Name"

    async def test_get_by_symbol(self, db_session):
        from investing_agent.db.repositories.company import CompanyRepository
        from investing_agent.schemas.company import CompanyCreate
        repo = CompanyRepository(db_session)
        await repo.upsert(CompanyCreate(symbol="TESTCO3", name="Test 3", exchange="BSE"))
        found = await repo.get_by_symbol("TESTCO3")
        assert found is not None
        assert found.name == "Test 3"

    async def test_get_by_symbol_not_found(self, db_session):
        from investing_agent.db.repositories.company import CompanyRepository
        repo = CompanyRepository(db_session)
        result = await repo.get_by_symbol("DOESNOTEXIST")
        assert result is None


class TestPortfolioRepository:
    async def test_save_from_broker(self, db_session):
        from investing_agent.db.repositories.portfolio import PortfolioRepository
        from investing_agent.gateway.mock import MockBrokerGateway

        broker = MockBrokerGateway()
        response = await broker.get_holdings()
        repo = PortfolioRepository(db_session)
        snapshot = await repo.save_from_broker("test_user", response)

        assert snapshot.id is not None
        assert snapshot.user_id == "test_user"
        assert snapshot.total_value is not None
        assert float(snapshot.total_value) > 0

    async def test_get_latest_returns_none_when_empty(self, db_session):
        from investing_agent.db.repositories.portfolio import PortfolioRepository
        repo = PortfolioRepository(db_session)
        result = await repo.get_latest("no_such_user")
        assert result is None


class TestThesisRepository:
    async def test_create_and_retrieve_thesis(self, db_session):
        from investing_agent.db.repositories.thesis import ThesisRepository
        from investing_agent.schemas.thesis import InvestmentThesisCreate
        repo = ThesisRepository(db_session)

        thesis = await repo.create(
            user_id="test_user",
            data=InvestmentThesisCreate(
                symbol="BEL",
                status="active",
                thesis="Defence duopoly. Strong order book.",
                horizon_months=24,
            ),
        )
        assert thesis.id is not None
        assert thesis.symbol == "BEL"

        retrieved = await repo.get_active("test_user", "BEL")
        assert retrieved is not None
        assert retrieved.thesis == "Defence duopoly. Strong order book."

    async def test_update_thesis(self, db_session):
        from investing_agent.db.repositories.thesis import ThesisRepository
        from investing_agent.schemas.thesis import InvestmentThesisCreate, InvestmentThesisUpdate
        repo = ThesisRepository(db_session)

        thesis = await repo.create(
            "test_user",
            InvestmentThesisCreate(symbol="HAL", status="watching"),
        )
        updated = await repo.update(
            thesis,
            InvestmentThesisUpdate(status="active", thesis="Upgraded to active"),
        )
        assert updated.status == "active"
        assert updated.thesis == "Upgraded to active"
