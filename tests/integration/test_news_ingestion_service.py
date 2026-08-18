from __future__ import annotations

"""Integration tests for NewsIngestionService.sync() against a real
PostgreSQL database, using a Fake NewsSource (no network).

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d

Covers the full fetch -> dedup -> match -> cluster pipeline end-to-end:
re-ingestion idempotency, BEL (unambiguous) and HAL (ambiguous-symbol)
matching, event clustering across two "publishers" in one sync, stale feed
detection, and publisher failure (SourceAccessError/SourceTransientError)
propagating into SourceReliability bookkeeping.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.news import CompanyAliasCreate
from investing_agent.services.ingestion.news import NewsIngestionService
from investing_agent.services.sources.interfaces import SourceAccessError, SourceTransientError
from investing_agent.services.sources.news_interfaces import NewsSource, RawNewsItem

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
async def hal(db_session):
    from investing_agent.db.repositories.company import CompanyRepository

    repo = CompanyRepository(db_session)
    return await repo.upsert(
        CompanyCreate(
            symbol=f"HAL-{uuid.uuid4().hex[:8]}", name="Hindustan Aeronautics", exchange="NSE"
        )
    )


@pytest.fixture
async def aliases(db_session, bel, hal):
    from investing_agent.db.repositories.company_alias import CompanyAliasRepository

    repo = CompanyAliasRepository(db_session)
    await repo.create(
        CompanyAliasCreate(
            company_id=bel.id, alias="Bharat Electronics", alias_type="full_name",
            match_confidence=Decimal("1.00"),
        )
    )
    await repo.create(
        CompanyAliasCreate(
            company_id=bel.id, alias="BEL", alias_type="symbol",
            match_confidence=Decimal("0.70"),
        )
    )
    await repo.create(
        CompanyAliasCreate(
            company_id=hal.id, alias="Hindustan Aeronautics", alias_type="full_name",
            match_confidence=Decimal("1.00"),
        )
    )
    await repo.create(
        CompanyAliasCreate(
            company_id=hal.id, alias="HAL", alias_type="symbol",
            match_confidence=Decimal("0.20"),
        )
    )
    await db_session.flush()


class FakeNewsSource(NewsSource):
    """In-memory NewsSource — returns a fixed list of items, or raises a
    configured error, no network involved."""

    def __init__(
        self,
        source_name: str,
        items: list[RawNewsItem] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.source_name = source_name
        self._items = items or []
        self._error = error

    async def fetch_all(self) -> list[RawNewsItem]:
        if self._error is not None:
            raise self._error
        return self._items


def _raw_item(headline: str, published_at: datetime, url_suffix: str) -> RawNewsItem:
    return RawNewsItem(
        headline=headline,
        feed_description=None,
        publisher="Test Publisher",
        source_url=f"https://example.com/{url_suffix}",
        published_at=published_at,
        raw={},
    )


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


class TestNewsIngestionServiceMatching:
    async def test_bel_unambiguous_match_creates_link(self, db_session, bel, hal, aliases) -> None:
        source = FakeNewsSource(
            "livemint",
            items=[_raw_item("Bharat Electronics wins Rs 500 crore order", NOW, "bel-1")],
        )
        service = NewsIngestionService(db_session, source)
        result = await service.sync()

        assert result.items_created == 1
        assert result.company_links_created == 1
        assert result.events_created == 1

    async def test_hal_bare_symbol_still_links_as_ambiguous(
        self, db_session, bel, hal, aliases
    ) -> None:
        source = FakeNewsSource(
            "livemint",
            items=[_raw_item("HAL shares rally after order win", NOW, "hal-1")],
        )
        service = NewsIngestionService(db_session, source)
        result = await service.sync()

        assert result.items_created == 1
        assert result.company_links_created == 1

        from investing_agent.db.repositories.news import NewsCompanyLinkRepository

        link_repo = NewsCompanyLinkRepository(db_session)
        links = await link_repo.list_by_company_since(hal.id, NOW - timedelta(days=1))
        assert len(links) == 1
        assert links[0].match_method == "alias_symbol_ambiguous"

    async def test_unrelated_item_creates_no_company_link(self, db_session, bel, hal, aliases) -> None:
        source = FakeNewsSource(
            "livemint",
            items=[_raw_item("Reliance Industries posts quarterly results", NOW, "ril-1")],
        )
        service = NewsIngestionService(db_session, source)
        result = await service.sync()

        assert result.items_created == 1
        assert result.company_links_created == 0
        assert result.events_created == 0


class TestNewsIngestionServiceClustering:
    async def test_two_publishers_same_story_cluster_into_one_event(
        self, db_session, bel, hal, aliases
    ) -> None:
        # Near-duplicate short-circuiting only compares within the same
        # source_name (see NewsIngestionService.sync's recent_same_source
        # filter), so two distinct publishers covering the same story must
        # be simulated as two separate syncs to reach event clustering
        # rather than being caught earlier as a near-duplicate NewsItem.
        first_service = NewsIngestionService(
            db_session,
            FakeNewsSource(
                "livemint",
                items=[
                    _raw_item(
                        "Bharat Electronics wins Rs 500 crore order from Navy", NOW, "bel-a"
                    )
                ],
            ),
        )
        first_result = await first_service.sync()

        second_service = NewsIngestionService(
            db_session,
            FakeNewsSource(
                "economic_times",
                items=[
                    _raw_item(
                        "Bharat Electronics bags Rs 500 crore order from the Navy",
                        NOW + timedelta(hours=1),
                        "bel-b",
                    )
                ],
            ),
        )
        second_result = await second_service.sync()

        assert first_result.items_created == 1
        assert second_result.items_created == 1
        assert first_result.events_created == 1
        assert second_result.events_extended == 1


class TestNewsIngestionServiceIdempotency:
    async def test_reingesting_same_items_creates_no_duplicates(
        self, db_session, bel, hal, aliases
    ) -> None:
        items = [_raw_item("Bharat Electronics wins Rs 500 crore order", NOW, "bel-reingest")]

        first_service = NewsIngestionService(db_session, FakeNewsSource("livemint", items=items))
        first_result = await first_service.sync()

        second_service = NewsIngestionService(db_session, FakeNewsSource("livemint", items=items))
        second_result = await second_service.sync()

        assert first_result.items_created == 1
        assert second_result.items_created == 0
        assert second_result.items_duplicate == 1

    async def test_near_duplicate_headline_same_source_not_recreated(
        self, db_session, bel, hal, aliases
    ) -> None:
        source = FakeNewsSource(
            "livemint",
            items=[
                _raw_item("Bharat Electronics wins Rs 500 crore order from Navy", NOW, "dup-a"),
                _raw_item(
                    "Bharat Electronics wins Rs 500 crore order from the Navy",
                    NOW + timedelta(minutes=5),
                    "dup-b",
                ),
            ],
        )
        service = NewsIngestionService(db_session, source)
        result = await service.sync()

        assert result.items_created == 1
        assert result.items_duplicate == 1


class TestNewsIngestionServiceStaleAndFailures:
    async def test_stale_feed_is_detected_and_recorded(self, db_session, bel, hal, aliases) -> None:
        old_time = NOW - timedelta(hours=200)
        source = FakeNewsSource(
            "livemint", items=[_raw_item("Old BEL news", old_time, "stale-1")]
        )
        service = NewsIngestionService(db_session, source)
        result = await service.sync()

        assert result.stale is True

        from investing_agent.db.repositories.research_memory import SourceReliabilityRepository

        reliability_repo = SourceReliabilityRepository(db_session)
        reliability = await reliability_repo.get_by_source_name("livemint")
        assert reliability is not None
        assert reliability.stale_feed_count == 1

    async def test_source_access_error_blocks_sync_and_records_failure(
        self, db_session, bel, hal, aliases
    ) -> None:
        source = FakeNewsSource(
            "livemint", error=SourceAccessError("403 blocked")
        )
        service = NewsIngestionService(db_session, source)
        result = await service.sync()

        assert result.blocked is True
        assert result.items_discovered == 0

        from investing_agent.db.repositories.research_memory import SourceReliabilityRepository

        reliability_repo = SourceReliabilityRepository(db_session)
        reliability = await reliability_repo.get_by_source_name("livemint")
        assert reliability is not None
        assert reliability.failed_fetches == 1

    async def test_source_transient_error_does_not_block_but_records_failure(
        self, db_session, bel, hal, aliases
    ) -> None:
        source = FakeNewsSource(
            "livemint", error=SourceTransientError("503 unavailable")
        )
        service = NewsIngestionService(db_session, source)
        result = await service.sync()

        assert result.blocked is False
        assert result.items_discovered == 0

        from investing_agent.db.repositories.research_memory import SourceReliabilityRepository

        reliability_repo = SourceReliabilityRepository(db_session)
        reliability = await reliability_repo.get_by_source_name("livemint")
        assert reliability is not None
        assert reliability.failed_fetches == 1
