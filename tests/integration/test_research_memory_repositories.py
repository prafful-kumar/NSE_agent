from __future__ import annotations

"""Integration tests for the Phase 4A relational research-memory
repositories: research_notes, thesis_changes, catalysts, risk_observations,
source_reliability.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d

Point-in-time queries here filter on created_at <= as_of (system-visibility,
not publisher-provenance — see db/repositories/research_memory.py module
docstring). created_at is set explicitly after each flush rather than
relying on wall-clock ordering: PostgreSQL's func.now() is transaction-scoped,
so two flushes inside the same uncommitted test transaction would otherwise
receive an identical timestamp.
"""

import uuid
from datetime import UTC, datetime

import pytest

from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.research_memory import (
    CatalystCreate,
    ResearchNoteCreate,
    ThesisChangeCreate,
)

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


class TestResearchNoteRepository:
    async def test_create_and_list_by_company(self, db_session, bel) -> None:
        from investing_agent.db.repositories.research_memory import ResearchNoteRepository

        repo = ResearchNoteRepository(db_session)
        await repo.create(
            ResearchNoteCreate(
                company_id=bel.id,
                text="Order book remains strong entering FY27.",
                effective_at=datetime(2026, 8, 10, tzinfo=UTC),
                created_by="analyst",
            )
        )
        await db_session.flush()

        notes = await repo.list_by_company(bel.id)
        assert len(notes) == 1
        assert notes[0].text == "Order book remains strong entering FY27."

    async def test_manual_research_note_point_in_time_query(self, db_session, bel) -> None:
        from investing_agent.db.repositories.research_memory import ResearchNoteRepository

        repo = ResearchNoteRepository(db_session)

        early_note = await repo.create(
            ResearchNoteCreate(
                company_id=bel.id,
                text="Initial thesis note.",
                effective_at=datetime(2026, 7, 1, tzinfo=UTC),
                created_by="analyst",
            )
        )
        early_note.created_at = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
        await db_session.flush()

        late_note = await repo.create(
            ResearchNoteCreate(
                company_id=bel.id,
                text="Follow-up note after order win.",
                effective_at=datetime(2026, 8, 10, tzinfo=UTC),
                created_by="analyst",
            )
        )
        late_note.created_at = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
        await db_session.flush()

        cutoff = datetime(2026, 7, 15, tzinfo=UTC)
        as_of_notes = await repo.list_by_company_as_of(bel.id, cutoff)
        ids = {n.id for n in as_of_notes}
        assert early_note.id in ids
        assert late_note.id not in ids

    async def test_list_by_company_since_filters_effective_at(self, db_session, bel) -> None:
        from investing_agent.db.repositories.research_memory import ResearchNoteRepository

        repo = ResearchNoteRepository(db_session)
        await repo.create(
            ResearchNoteCreate(
                company_id=bel.id,
                text="Old note.",
                effective_at=datetime(2026, 1, 1, tzinfo=UTC),
                created_by="analyst",
            )
        )
        recent = await repo.create(
            ResearchNoteCreate(
                company_id=bel.id,
                text="Recent note.",
                effective_at=datetime(2026, 8, 10, tzinfo=UTC),
                created_by="analyst",
            )
        )
        await db_session.flush()

        since = datetime(2026, 6, 1, tzinfo=UTC)
        notes = await repo.list_by_company(bel.id, since=since)
        assert [n.id for n in notes] == [recent.id]


class TestThesisChangeRepository:
    async def test_create_and_point_in_time_query(self, db_session, bel) -> None:
        from investing_agent.db.repositories.research_memory import ThesisChangeRepository

        repo = ThesisChangeRepository(db_session)
        early = await repo.create(
            ThesisChangeCreate(
                company_id=bel.id,
                change_type="initiated",
                reason="Initial coverage.",
                effective_at=datetime(2026, 6, 1, tzinfo=UTC),
            )
        )
        early.created_at = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
        await db_session.flush()

        late = await repo.create(
            ThesisChangeCreate(
                company_id=bel.id,
                change_type="catalyst_added",
                reason="New defence order raises revenue visibility.",
                effective_at=datetime(2026, 8, 10, tzinfo=UTC),
            )
        )
        late.created_at = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
        await db_session.flush()

        cutoff = datetime(2026, 7, 1, tzinfo=UTC)
        as_of_changes = await repo.list_by_company_as_of(bel.id, cutoff)
        ids = {c.id for c in as_of_changes}
        assert early.id in ids
        assert late.id not in ids


class TestCatalystAndRiskActiveListing:
    async def test_catalyst_list_active_excludes_realized(self, db_session, bel) -> None:
        from investing_agent.db.repositories.research_memory import CatalystRepository

        repo = CatalystRepository(db_session)
        active = await repo.create(
            CatalystCreate(
                company_id=bel.id,
                description="Expected large defence order in Q3.",
                catalyst_type="order_win",
                status="active",
            )
        )
        await repo.create(
            CatalystCreate(
                company_id=bel.id,
                description="Order already realized.",
                catalyst_type="order_win",
                status="realized",
            )
        )
        await db_session.flush()

        active_catalysts = await repo.list_active(bel.id)
        ids = {c.id for c in active_catalysts}
        assert ids == {active.id}

    async def test_risk_observation_list_active_excludes_resolved(self, db_session, bel) -> None:
        from investing_agent.db.repositories.research_memory import RiskObservationRepository
        from investing_agent.schemas.research_memory import RiskObservationCreate

        repo = RiskObservationRepository(db_session)
        active = await repo.create(
            RiskObservationCreate(
                company_id=bel.id,
                description="Order execution delay risk.",
                risk_type="execution",
                status="active",
            )
        )
        await repo.create(
            RiskObservationCreate(
                company_id=bel.id,
                description="Resolved supply chain issue.",
                risk_type="supply_chain",
                status="resolved",
            )
        )
        await db_session.flush()

        active_risks = await repo.list_active(bel.id)
        ids = {r.id for r in active_risks}
        assert ids == {active.id}


class TestSourceReliabilityRepository:
    async def test_record_success_increments_and_sets_timestamp(self, db_session) -> None:
        from investing_agent.db.repositories.research_memory import SourceReliabilityRepository

        source_name = f"livemint-{uuid.uuid4().hex[:8]}"
        repo = SourceReliabilityRepository(db_session)

        row = await repo.record_success(source_name)
        assert row.successful_fetches == 1
        assert row.last_success_at is not None

        row2 = await repo.record_success(source_name)
        assert row2.successful_fetches == 2
        assert row2.id == row.id

    async def test_record_failure_increments_and_sets_timestamp(self, db_session) -> None:
        from investing_agent.db.repositories.research_memory import SourceReliabilityRepository

        source_name = f"economic_times-{uuid.uuid4().hex[:8]}"
        repo = SourceReliabilityRepository(db_session)

        row = await repo.record_failure(source_name)
        assert row.failed_fetches == 1
        assert row.last_failure_at is not None

    async def test_record_stale_increments_counter(self, db_session) -> None:
        from investing_agent.db.repositories.research_memory import SourceReliabilityRepository

        source_name = f"livemint-{uuid.uuid4().hex[:8]}"
        repo = SourceReliabilityRepository(db_session)

        row = await repo.record_stale(source_name)
        assert row.stale_feed_count == 1

    async def test_get_or_create_for_source_is_idempotent(self, db_session) -> None:
        from investing_agent.db.repositories.research_memory import SourceReliabilityRepository

        source_name = f"google_news-{uuid.uuid4().hex[:8]}"
        repo = SourceReliabilityRepository(db_session)

        first = await repo.get_or_create_for_source(source_name)
        second = await repo.get_or_create_for_source(source_name)
        assert first.id == second.id

    async def test_get_by_source_name_returns_none_when_missing(self, db_session) -> None:
        from investing_agent.db.repositories.research_memory import SourceReliabilityRepository

        repo = SourceReliabilityRepository(db_session)
        found = await repo.get_by_source_name(f"nonexistent-{uuid.uuid4().hex[:8]}")
        assert found is None
