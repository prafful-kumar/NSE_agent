from __future__ import annotations

"""Integration tests for db/repositories/interpretation.py::EventInterpretationRepository
against a real PostgreSQL database.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.interpretation import EventInterpretationCreate
from investing_agent.schemas.news import NewsEventCreate

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
async def bel(db_session):
    from investing_agent.db.repositories.company import CompanyRepository

    repo = CompanyRepository(db_session)
    return await repo.upsert(
        CompanyCreate(
            symbol=f"BEL-{uuid.uuid4().hex[:8]}", name="Bharat Electronics", exchange="NSE"
        )
    )


@pytest.fixture
async def news_event(db_session, bel):
    from investing_agent.db.repositories.news import NewsEventRepository

    repo = NewsEventRepository(db_session)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    return await repo.create(
        NewsEventCreate(
            primary_company_id=bel.id,
            first_seen_at=now,
            last_seen_at=now,
            representative_headline="BEL wins order worth Rs 500 crore",
        )
    )


def _create_data(news_event_id, company_id, **overrides) -> EventInterpretationCreate:
    defaults = dict(
        news_event_id=news_event_id,
        company_id=company_id,
        impact_classification={"revenue": {"direction": "positive", "magnitude": "medium"}},
        rationale="Order win reported.",
        candidate_catalyst={
            "description": "Order win", "catalyst_type": "order_win", "status": "active",
        },
        extraction_method="DETERMINISTIC",
        extractor_version="rules-v1",
        confidence=Decimal("0.60"),
    )
    defaults.update(overrides)
    return EventInterpretationCreate(**defaults)


class TestEventInterpretationRepositoryCRUD:
    async def test_create_and_get(self, db_session, bel, news_event) -> None:
        from investing_agent.db.repositories.interpretation import EventInterpretationRepository

        repo = EventInterpretationRepository(db_session)
        created = await repo.create(_create_data(news_event.id, bel.id))
        await db_session.flush()

        fetched = await repo.get(created.id)
        assert fetched is not None
        assert fetched.review_status == "pending"
        assert fetched.rationale == "Order win reported."

    async def test_list_by_event_and_company(self, db_session, bel, news_event) -> None:
        from investing_agent.db.repositories.interpretation import EventInterpretationRepository

        repo = EventInterpretationRepository(db_session)
        await repo.create(_create_data(news_event.id, bel.id))
        await db_session.flush()

        found = await repo.list_by_event_and_company(news_event.id, bel.id)
        assert len(found) == 1

        other_company_id = uuid.uuid4()
        not_found = await repo.list_by_event_and_company(news_event.id, other_company_id)
        assert not_found == []

    async def test_list_pending_filters_by_status_and_company(
        self, db_session, bel, news_event
    ) -> None:
        from investing_agent.db.repositories.interpretation import EventInterpretationRepository

        repo = EventInterpretationRepository(db_session)
        pending = await repo.create(_create_data(news_event.id, bel.id))
        accepted = await repo.create(
            _create_data(news_event.id, bel.id, rationale="Second interpretation.")
        )
        await repo.mark_accepted(accepted, reviewed_by="analyst")
        await db_session.flush()

        results = await repo.list_pending(company_id=bel.id)
        ids = {r.id for r in results}
        assert pending.id in ids
        assert accepted.id not in ids


class TestEventInterpretationRepositoryReviewDecisions:
    async def test_mark_accepted_sets_fields(self, db_session, bel, news_event) -> None:
        from investing_agent.db.repositories.interpretation import EventInterpretationRepository
        from investing_agent.db.repositories.research_memory import CatalystRepository
        from investing_agent.schemas.research_memory import CatalystCreate

        repo = EventInterpretationRepository(db_session)
        interpretation = await repo.create(_create_data(news_event.id, bel.id))
        await db_session.flush()

        # resulting_catalyst_id is a real FK — must reference an actual
        # catalysts row, same as review-interpretation --accept creates one.
        catalyst = await CatalystRepository(db_session).create(
            CatalystCreate(
                company_id=bel.id, description="Order win", catalyst_type="order_win",
            )
        )
        await db_session.flush()

        updated = await repo.mark_accepted(
            interpretation, reviewed_by="analyst", resulting_catalyst_id=catalyst.id
        )

        assert updated.review_status == "accepted"
        assert updated.reviewed_by == "analyst"
        assert updated.reviewed_at is not None
        assert updated.resulting_catalyst_id == catalyst.id

    async def test_mark_rejected_sets_fields(self, db_session, bel, news_event) -> None:
        from investing_agent.db.repositories.interpretation import EventInterpretationRepository

        repo = EventInterpretationRepository(db_session)
        interpretation = await repo.create(_create_data(news_event.id, bel.id))
        await db_session.flush()

        updated = await repo.mark_rejected(interpretation, reviewed_by="analyst")

        assert updated.review_status == "rejected"
        assert updated.reviewed_by == "analyst"
        assert updated.resulting_catalyst_id is None
