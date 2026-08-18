from __future__ import annotations

"""Integration tests for the Phase 4A news repositories: news_items,
news_events, news_event_items, news_company_links, company_aliases.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d

Covers persistence contracts that only a real database can prove:
get_or_create idempotency (re-ingestion), unique constraints, and
clustering-membership joins.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.news import (
    CompanyAliasCreate,
    NewsCompanyLinkCreate,
    NewsEventCreate,
    NewsItemCreate,
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


def _news_item_create(*, source_url: str, headline: str = "BEL wins order") -> NewsItemCreate:
    return NewsItemCreate(
        headline=headline,
        feed_description=None,
        publisher="Test Publisher",
        source_name="livemint",
        source_url=source_url,
        published_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
        content_hash=f"hash-{uuid.uuid4().hex}",
    )


class TestNewsItemRepositoryIdempotency:
    async def test_get_or_create_is_idempotent_on_same_source_url(self, db_session) -> None:
        from investing_agent.db.repositories.news import NewsItemRepository

        repo = NewsItemRepository(db_session)
        create = _news_item_create(source_url="https://example.com/bel-order-1")

        first, first_created = await repo.get_or_create(create)
        second, second_created = await repo.get_or_create(create)
        await db_session.flush()

        assert first_created is True
        assert second_created is False
        assert first.id == second.id

    async def test_different_source_url_creates_new_item(self, db_session) -> None:
        from investing_agent.db.repositories.news import NewsItemRepository

        repo = NewsItemRepository(db_session)
        first, _ = await repo.get_or_create(
            _news_item_create(source_url="https://example.com/bel-order-a")
        )
        second, created = await repo.get_or_create(
            _news_item_create(source_url="https://example.com/bel-order-b")
        )
        await db_session.flush()

        assert created is True
        assert first.id != second.id

    async def test_list_by_content_hash_finds_exact_duplicate(self, db_session) -> None:
        from investing_agent.db.repositories.news import NewsItemRepository

        repo = NewsItemRepository(db_session)
        create = _news_item_create(source_url="https://example.com/bel-order-hash")
        item, _ = await repo.get_or_create(create)
        await db_session.flush()

        found = await repo.list_by_content_hash(create.content_hash, "livemint")
        assert len(found) == 1
        assert found[0].id == item.id


class TestNewsEventClustering:
    async def test_event_item_link_persists_membership(self, db_session, bel) -> None:
        from investing_agent.db.repositories.news import (
            NewsEventItemRepository,
            NewsEventRepository,
            NewsItemRepository,
        )

        item_repo = NewsItemRepository(db_session)
        item, _ = await item_repo.get_or_create(
            _news_item_create(source_url="https://example.com/bel-event-1")
        )

        event_repo = NewsEventRepository(db_session)
        event = await event_repo.create(
            NewsEventCreate(
                primary_company_id=bel.id,
                first_seen_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
                last_seen_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
                representative_headline="BEL wins order",
            )
        )

        link_repo = NewsEventItemRepository(db_session)
        await link_repo.link(event.id, item.id)
        await db_session.flush()

        members = await link_repo.list_items_for_event(event.id)
        assert len(members) == 1
        assert members[0].news_item_id == item.id

    async def test_link_is_idempotent(self, db_session, bel) -> None:
        from investing_agent.db.repositories.news import (
            NewsEventItemRepository,
            NewsEventRepository,
            NewsItemRepository,
        )

        item_repo = NewsItemRepository(db_session)
        item, _ = await item_repo.get_or_create(
            _news_item_create(source_url="https://example.com/bel-event-2")
        )
        event_repo = NewsEventRepository(db_session)
        event = await event_repo.create(
            NewsEventCreate(
                primary_company_id=bel.id,
                first_seen_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
                last_seen_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
                representative_headline="BEL wins order",
            )
        )

        link_repo = NewsEventItemRepository(db_session)
        await link_repo.link(event.id, item.id)
        await link_repo.link(event.id, item.id)
        await db_session.flush()

        members = await link_repo.list_items_for_event(event.id)
        assert len(members) == 1

    async def test_find_recent_candidate_filters_by_company_status_and_window(
        self, db_session, bel
    ) -> None:
        from investing_agent.db.repositories.news import NewsEventRepository

        event_repo = NewsEventRepository(db_session)
        recent_time = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
        stale_time = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

        recent_event = await event_repo.create(
            NewsEventCreate(
                primary_company_id=bel.id,
                first_seen_at=recent_time,
                last_seen_at=recent_time,
                representative_headline="Recent event",
            )
        )
        old_event = await event_repo.create(
            NewsEventCreate(
                primary_company_id=bel.id,
                first_seen_at=stale_time,
                last_seen_at=stale_time,
                representative_headline="Old event",
            )
        )
        await db_session.flush()

        candidates = await event_repo.find_recent_candidate(
            bel.id, since=recent_time - timedelta(hours=1)
        )
        candidate_ids = {c.id for c in candidates}
        assert recent_event.id in candidate_ids
        assert old_event.id not in candidate_ids

    async def test_list_by_company_as_of_excludes_future_events(self, db_session, bel) -> None:
        from investing_agent.db.repositories.news import NewsEventRepository

        event_repo = NewsEventRepository(db_session)
        early = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        late = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

        early_event = await event_repo.create(
            NewsEventCreate(
                primary_company_id=bel.id,
                first_seen_at=early,
                last_seen_at=early,
                representative_headline="Early event",
            )
        )
        late_event = await event_repo.create(
            NewsEventCreate(
                primary_company_id=bel.id,
                first_seen_at=late,
                last_seen_at=late,
                representative_headline="Late event",
            )
        )
        await db_session.flush()

        cutoff = datetime(2026, 8, 10, tzinfo=UTC)
        as_of_events = await event_repo.list_by_company_as_of(bel.id, cutoff)
        ids = {e.id for e in as_of_events}
        assert early_event.id in ids
        assert late_event.id not in ids


class TestNewsCompanyLinkRepository:
    async def test_get_or_create_is_idempotent(self, db_session, bel) -> None:
        from investing_agent.db.repositories.news import (
            NewsCompanyLinkRepository,
            NewsItemRepository,
        )

        item_repo = NewsItemRepository(db_session)
        item, _ = await item_repo.get_or_create(
            _news_item_create(source_url="https://example.com/bel-link-1")
        )

        link_repo = NewsCompanyLinkRepository(db_session)
        create = NewsCompanyLinkCreate(
            news_item_id=item.id,
            company_id=bel.id,
            relevance_score=Decimal("0.70"),
            match_method="alias_symbol",
        )
        first, first_created = await link_repo.get_or_create(create)
        second, second_created = await link_repo.get_or_create(create)
        await db_session.flush()

        assert first_created is True
        assert second_created is False
        assert first.id == second.id

    async def test_list_by_company_since_joins_on_discovered_at(self, db_session, bel) -> None:
        from investing_agent.db.repositories.news import (
            NewsCompanyLinkRepository,
            NewsItemRepository,
        )

        item_repo = NewsItemRepository(db_session)
        item, _ = await item_repo.get_or_create(
            _news_item_create(source_url="https://example.com/bel-link-2")
        )
        link_repo = NewsCompanyLinkRepository(db_session)
        await link_repo.create(
            NewsCompanyLinkCreate(
                news_item_id=item.id,
                company_id=bel.id,
                relevance_score=Decimal("1.00"),
                match_method="alias_full_name",
            )
        )
        await db_session.flush()

        since = datetime.now(UTC) - timedelta(days=1)
        links = await link_repo.list_by_company_since(bel.id, since)
        assert len(links) == 1
        assert links[0].company_id == bel.id


class TestCompanyAliasRepository:
    async def test_create_and_list_by_company(self, db_session, bel) -> None:
        from investing_agent.db.repositories.company_alias import CompanyAliasRepository

        repo = CompanyAliasRepository(db_session)
        await repo.create(
            CompanyAliasCreate(
                company_id=bel.id,
                alias="Bharat Electronics",
                alias_type="full_name",
                match_confidence=Decimal("1.00"),
            )
        )
        await repo.create(
            CompanyAliasCreate(
                company_id=bel.id,
                alias="BEL",
                alias_type="symbol",
                match_confidence=Decimal("0.70"),
            )
        )
        await db_session.flush()

        aliases = await repo.list_by_company(bel.id)
        assert len(aliases) == 2

    async def test_get_by_company_and_alias_finds_existing(self, db_session, bel) -> None:
        from investing_agent.db.repositories.company_alias import CompanyAliasRepository

        repo = CompanyAliasRepository(db_session)
        await repo.create(
            CompanyAliasCreate(
                company_id=bel.id,
                alias="Bharat Electronics",
                alias_type="full_name",
                match_confidence=Decimal("1.00"),
            )
        )
        await db_session.flush()

        found = await repo.get_by_company_and_alias(bel.id, "Bharat Electronics")
        assert found is not None
        missing = await repo.get_by_company_and_alias(bel.id, "Nonexistent Corp")
        assert missing is None

    async def test_list_active_excludes_inactive_aliases(self, db_session, bel) -> None:
        from investing_agent.db.repositories.company_alias import CompanyAliasRepository

        repo = CompanyAliasRepository(db_session)
        await repo.create(
            CompanyAliasCreate(
                company_id=bel.id,
                alias="Bharat Electronics",
                alias_type="full_name",
                match_confidence=Decimal("1.00"),
                is_active=True,
            )
        )
        await repo.create(
            CompanyAliasCreate(
                company_id=bel.id,
                alias="Retired Alias",
                alias_type="full_name",
                match_confidence=Decimal("1.00"),
                is_active=False,
            )
        )
        await db_session.flush()

        active = await repo.list_active()
        active_texts = {a.alias for a in active}
        assert "Bharat Electronics" in active_texts
        assert "Retired Alias" not in active_texts
