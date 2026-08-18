from __future__ import annotations

"""Repositories for Phase 4A news ingestion: news_items, news_events,
news_event_items, news_company_links.

Idempotency lives in NewsItemRepository.get_or_create, mirroring
SourceDocumentRepository — re-polling the same feed entry (same
source_name + source_url) is a no-op, not a duplicate row.
"""

import uuid
from datetime import datetime

from sqlalchemy import select

from investing_agent.db.models import NewsCompanyLink, NewsEvent, NewsEventItem, NewsItem
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.news import (
    NewsCompanyLinkCreate,
    NewsEventCreate,
    NewsItemCreate,
)


class NewsItemRepository(BaseRepository[NewsItem]):
    model = NewsItem

    async def get_by_source_url(
        self, source_name: str, source_url: str
    ) -> NewsItem | None:
        result = await self.session.execute(
            select(NewsItem).where(
                NewsItem.source_name == source_name, NewsItem.source_url == source_url
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, data: NewsItemCreate) -> tuple[NewsItem, bool]:
        """Returns (item, was_created). Idempotent on (source_name, source_url)."""
        existing = await self.get_by_source_url(data.source_name, data.source_url)
        if existing:
            return existing, False
        row = NewsItem(**data.model_dump())
        await self.add(row)
        return row, True

    async def list_by_content_hash(
        self, content_hash: str, source_name: str | None = None
    ) -> list[NewsItem]:
        stmt = select(NewsItem).where(NewsItem.content_hash == content_hash)
        if source_name:
            stmt = stmt.where(NewsItem.source_name == source_name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_recent(self, since: datetime, limit: int = 500) -> list[NewsItem]:
        result = await self.session.execute(
            select(NewsItem)
            .where(NewsItem.discovered_at >= since)
            .order_by(NewsItem.discovered_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


class NewsEventRepository(BaseRepository[NewsEvent]):
    model = NewsEvent

    async def create(self, data: NewsEventCreate) -> NewsEvent:
        row = NewsEvent(**data.model_dump())
        return await self.add(row)

    async def list_by_company(
        self, company_id: uuid.UUID, since: datetime | None = None
    ) -> list[NewsEvent]:
        stmt = select(NewsEvent).where(NewsEvent.primary_company_id == company_id)
        if since is not None:
            stmt = stmt.where(NewsEvent.last_seen_at >= since)
        stmt = stmt.order_by(NewsEvent.last_seen_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_company_as_of(
        self, company_id: uuid.UUID, as_of: datetime
    ) -> list[NewsEvent]:
        result = await self.session.execute(
            select(NewsEvent)
            .where(
                NewsEvent.primary_company_id == company_id,
                NewsEvent.first_seen_at <= as_of,
            )
            .order_by(NewsEvent.last_seen_at.desc())
        )
        return list(result.scalars().all())

    async def list_since(self, since: datetime) -> list[NewsEvent]:
        """All events (any company) with activity since a timestamp — used by
        InterpretationService when no single company is specified. Excludes
        events with no primary_company_id (interpretation is always scoped
        to a company)."""
        result = await self.session.execute(
            select(NewsEvent).where(
                NewsEvent.primary_company_id.isnot(None),
                NewsEvent.last_seen_at >= since,
            )
        )
        return list(result.scalars().all())

    async def find_recent_candidate(
        self, company_id: uuid.UUID, since: datetime
    ) -> list[NewsEvent]:
        """Open clustering candidates for a company within a recency window
        — used by the dedup/clustering service to decide whether a new
        NewsItem joins an existing event or starts a new one."""
        result = await self.session.execute(
            select(NewsEvent).where(
                NewsEvent.primary_company_id == company_id,
                NewsEvent.status == "active",
                NewsEvent.last_seen_at >= since,
            )
        )
        return list(result.scalars().all())


class NewsEventItemRepository(BaseRepository[NewsEventItem]):
    model = NewsEventItem

    async def link(self, news_event_id: uuid.UUID, news_item_id: uuid.UUID) -> NewsEventItem:
        existing = await self.session.execute(
            select(NewsEventItem).where(
                NewsEventItem.news_event_id == news_event_id,
                NewsEventItem.news_item_id == news_item_id,
            )
        )
        row = existing.scalar_one_or_none()
        if row:
            return row
        row = NewsEventItem(news_event_id=news_event_id, news_item_id=news_item_id)
        return await self.add(row)

    async def list_items_for_event(self, news_event_id: uuid.UUID) -> list[NewsEventItem]:
        result = await self.session.execute(
            select(NewsEventItem).where(NewsEventItem.news_event_id == news_event_id)
        )
        return list(result.scalars().all())


class NewsCompanyLinkRepository(BaseRepository[NewsCompanyLink]):
    model = NewsCompanyLink

    async def create(self, data: NewsCompanyLinkCreate) -> NewsCompanyLink:
        row = NewsCompanyLink(**data.model_dump())
        return await self.add(row)

    async def get_or_create(self, data: NewsCompanyLinkCreate) -> tuple[NewsCompanyLink, bool]:
        result = await self.session.execute(
            select(NewsCompanyLink).where(
                NewsCompanyLink.news_item_id == data.news_item_id,
                NewsCompanyLink.company_id == data.company_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing, False
        return await self.create(data), True

    async def list_by_company_since(
        self, company_id: uuid.UUID, since: datetime
    ) -> list[NewsCompanyLink]:
        result = await self.session.execute(
            select(NewsCompanyLink)
            .join(NewsItem, NewsCompanyLink.news_item_id == NewsItem.id)
            .where(NewsCompanyLink.company_id == company_id, NewsItem.discovered_at >= since)
        )
        return list(result.scalars().all())
