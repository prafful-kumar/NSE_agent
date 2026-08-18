from __future__ import annotations

"""Schemas for Phase 4A news ingestion: company_aliases, news_items,
news_events, news_event_items, news_company_links.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from investing_agent.schemas.common import BaseSchema, TimestampedSchema

AliasType = Literal["symbol", "full_name", "short_name"]
MatchMethod = Literal[
    "alias_full_name", "alias_short_name", "alias_symbol", "alias_symbol_ambiguous", "manual"
]
NewsSourceName = Literal["livemint", "economic_times", "google_news", "manual"]


# ── Company aliases ───────────────────────────────────────────────────────

class CompanyAliasCreate(BaseSchema):
    company_id: uuid.UUID
    alias: str
    alias_type: AliasType
    match_confidence: Decimal = Decimal("1.00")
    is_active: bool = True


class CompanyAliasRead(TimestampedSchema):
    id: uuid.UUID
    company_id: uuid.UUID
    alias: str
    alias_type: str
    match_confidence: Decimal
    is_active: bool


# ── News items ────────────────────────────────────────────────────────────

class NewsItemCreate(BaseSchema):
    headline: str
    feed_description: str | None = None
    publisher: str | None = None
    source_name: NewsSourceName
    source_url: str
    canonical_url: str | None = None
    published_at: datetime | None = None
    content_hash: str
    raw_metadata: dict | None = None


class NewsItemRead(TimestampedSchema):
    id: uuid.UUID
    headline: str
    feed_description: str | None
    publisher: str | None
    source_name: str
    source_url: str
    canonical_url: str | None
    published_at: datetime | None
    discovered_at: datetime
    content_hash: str
    raw_metadata: dict | None


# ── News events ───────────────────────────────────────────────────────────

class NewsEventCreate(BaseSchema):
    event_type: str = "unclassified"
    primary_company_id: uuid.UUID | None = None
    event_date: date | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    importance: str = "unclassified"
    status: str = "active"
    representative_headline: str


class NewsEventRead(TimestampedSchema):
    id: uuid.UUID
    event_type: str
    primary_company_id: uuid.UUID | None
    event_date: date | None
    first_seen_at: datetime
    last_seen_at: datetime
    importance: str
    status: str
    representative_headline: str


class NewsEventItemCreate(BaseSchema):
    news_event_id: uuid.UUID
    news_item_id: uuid.UUID


# ── News company links ────────────────────────────────────────────────────

class NewsCompanyLinkCreate(BaseSchema):
    news_item_id: uuid.UUID
    company_id: uuid.UUID
    relevance_score: Decimal
    match_method: MatchMethod


class NewsCompanyLinkRead(TimestampedSchema):
    id: uuid.UUID
    news_item_id: uuid.UUID
    company_id: uuid.UUID
    relevance_score: Decimal
    match_method: str
