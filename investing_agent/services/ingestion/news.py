from __future__ import annotations

"""NewsIngestionService: fetch -> dedup -> archive metadata -> match ->
cluster, for a single Phase 4A NewsSource adapter.

Deliberately narrow scope, matching the Phase 4A spec: no article scraping,
no embeddings, no LLM classification. Two dedup layers run before a
NewsItem row is even created:

1. Exact/near-duplicate NewsItem detection — content_hash equality (same
   source, same normalized headline) or difflib near-duplicate against
   recently ingested items from the same source (services/dedup/news_dedup).
   Both short-circuit before get_or_create, so a republished/reworded
   headline from the same outlet never becomes a second NewsItem row.
2. NewsEvent clustering — once a genuinely new NewsItem is created and
   matched to one or more companies, it either joins an existing NewsEvent
   (same company, recent, similar headline, no conflicting numeric terms)
   or starts a new one. NewsItem rows are never merged across publishers;
   only NewsEvent membership does the cross-publisher grouping.

Failure handling mirrors FilingIngestionService: SourceAccessError and
SourceTransientError are caught here and degrade the sync gracefully
(result.blocked) rather than raising — one bad fetch must not corrupt
SourceReliability bookkeeping for a source that otherwise works.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import NewsEvent, NewsItem
from investing_agent.db.repositories.company_alias import CompanyAliasRepository
from investing_agent.db.repositories.news import (
    NewsCompanyLinkRepository,
    NewsEventItemRepository,
    NewsEventRepository,
    NewsItemRepository,
)
from investing_agent.db.repositories.research_memory import SourceReliabilityRepository
from investing_agent.schemas.news import NewsCompanyLinkCreate, NewsEventCreate, NewsItemCreate
from investing_agent.services.dedup.news_dedup import (
    CLUSTER_TIME_WINDOW,
    compute_content_hash,
    find_best_cluster,
    find_near_duplicate,
)
from investing_agent.services.matching.company_matcher import CompanyMatcher
from investing_agent.services.sources.interfaces import SourceAccessError, SourceTransientError
from investing_agent.services.sources.news_interfaces import NewsSource, RawNewsItem

log = structlog.get_logger(__name__)

# A feed responding 200 whose newest item is older than this is reported as
# stale (SourceReliability.stale_feed_count), not silently treated as "no
# news today" — distinguishes a broken/frozen feed from a genuinely quiet one.
STALE_FEED_MAX_AGE = timedelta(hours=48)
# Same window as event clustering: only compare against recently-ingested
# items from this source when checking for a near-duplicate headline.
RECENT_ITEMS_LOOKBACK = CLUSTER_TIME_WINDOW


@dataclass
class NewsSyncResult:
    source_name: str
    items_discovered: int = 0
    items_created: int = 0
    items_duplicate: int = 0
    company_links_created: int = 0
    events_created: int = 0
    events_extended: int = 0
    blocked: bool = False
    stale: bool = False
    created_items: list[NewsItem] = field(default_factory=list)


class NewsIngestionService:
    def __init__(self, session: AsyncSession, source: NewsSource) -> None:
        self._session = session
        self._source = source

    async def sync(self) -> NewsSyncResult:
        source_name = self._source.source_name
        result = NewsSyncResult(source_name=source_name)
        reliability_repo = SourceReliabilityRepository(self._session)

        try:
            raw_items = await self._source.fetch_all()
        except SourceAccessError:
            log.warning("news_sync.blocked", source=source_name)
            result.blocked = True
            await reliability_repo.record_failure(source_name)
            return result
        except SourceTransientError:
            log.warning("news_sync.transient_failure", source=source_name)
            await reliability_repo.record_failure(source_name)
            return result

        result.items_discovered = len(raw_items)
        await reliability_repo.record_success(source_name)
        if self._is_stale(raw_items):
            result.stale = True
            await reliability_repo.record_stale(source_name)

        item_repo = NewsItemRepository(self._session)
        recent_same_source = [
            existing
            for existing in await item_repo.list_recent(
                since=datetime.now(UTC) - RECENT_ITEMS_LOOKBACK
            )
            if existing.source_name == source_name
        ]

        alias_repo = CompanyAliasRepository(self._session)
        matcher = CompanyMatcher(await alias_repo.list_active())

        for raw in raw_items:
            item = await self._ingest_item(
                raw, source_name, item_repo, recent_same_source, matcher, result
            )
            if item is not None:
                recent_same_source.append(item)

        return result

    def _is_stale(self, raw_items: list[RawNewsItem]) -> bool:
        published_dates = [it.published_at for it in raw_items if it.published_at is not None]
        if not published_dates:
            return False
        newest = max(published_dates)
        return datetime.now(UTC) - newest > STALE_FEED_MAX_AGE

    async def _ingest_item(
        self,
        raw: RawNewsItem,
        source_name: str,
        item_repo: NewsItemRepository,
        recent_same_source: list[NewsItem],
        matcher: CompanyMatcher,
        result: NewsSyncResult,
    ) -> NewsItem | None:
        content_hash = compute_content_hash(source_name, raw.headline)

        if await item_repo.list_by_content_hash(content_hash, source_name):
            result.items_duplicate += 1
            return None

        if find_near_duplicate(raw.headline, recent_same_source) is not None:
            result.items_duplicate += 1
            return None

        item, was_created = await item_repo.get_or_create(
            NewsItemCreate(
                headline=raw.headline,
                feed_description=raw.feed_description,
                publisher=raw.publisher,
                source_name=source_name,
                source_url=raw.source_url,
                published_at=raw.published_at,
                content_hash=content_hash,
                raw_metadata=raw.raw,
            )
        )
        if not was_created:
            result.items_duplicate += 1
            return item

        result.items_created += 1
        result.created_items.append(item)

        link_repo = NewsCompanyLinkRepository(self._session)
        matches = matcher.match(f"{raw.headline} {raw.feed_description or ''}")
        for match in matches:
            _link, link_created = await link_repo.get_or_create(
                NewsCompanyLinkCreate(
                    news_item_id=item.id,
                    company_id=match.company_id,
                    relevance_score=match.relevance_score,
                    match_method=match.match_method,
                )
            )
            if link_created:
                result.company_links_created += 1
            await self._cluster_event(item, match.company_id, result)

        return item

    async def _cluster_event(
        self, item: NewsItem, company_id: uuid.UUID, result: NewsSyncResult
    ) -> None:
        _event, was_created = await cluster_event(self._session, item, company_id)
        if was_created:
            result.events_created += 1
        else:
            result.events_extended += 1

    async def aclose(self) -> None:
        aclose = getattr(self._source, "aclose", None)
        if aclose is not None:
            await aclose()


async def cluster_event(
    session: AsyncSession, item: NewsItem, company_id: uuid.UUID
) -> tuple[NewsEvent, bool]:
    """Links item into an existing NewsEvent for company_id if a clustering
    candidate matches, else creates a new one. Returns (event, was_created).

    A free function, not a method, so the record-news-url CLI command can
    cluster a manually-entered NewsItem identically to an RSS-discovered
    one without depending on a NewsIngestionService instance (which expects
    a NewsSource to poll, which a manual entry doesn't have).
    """
    reference_time = item.published_at or item.discovered_at
    event_repo = NewsEventRepository(session)
    candidates = await event_repo.find_recent_candidate(
        company_id, reference_time - CLUSTER_TIME_WINDOW
    )
    best = find_best_cluster(item, candidates)

    event_item_repo = NewsEventItemRepository(session)
    if best is not None:
        if reference_time > best.last_seen_at:
            best.last_seen_at = reference_time
            await session.flush()
        await event_item_repo.link(best.id, item.id)
        return best, False

    new_event = await event_repo.create(
        NewsEventCreate(
            primary_company_id=company_id,
            first_seen_at=reference_time,
            last_seen_at=reference_time,
            representative_headline=item.headline,
        )
    )
    await event_item_repo.link(new_event.id, item.id)
    return new_event, True
