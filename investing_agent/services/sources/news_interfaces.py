from __future__ import annotations

"""NewsSource — Phase 4A Tier-2 news capability interface.

Kept deliberately separate from services/sources/interfaces.py (Tier-1
filing/financial-result/corporate-action capabilities): news is RSS-derived
metadata, not an archived primary-source document, and every adapter here
must honor the same hard constraint from the Phase 4 bake-off — no
article-page scraping, no paywall bypass, no persistence of full article
bodies. RawNewsItem therefore never carries a body/content field, only
feed-provided metadata (headline, snippet, publisher, link, pubDate).

Reuses SourceAccessError/SourceTransientError from
services/sources/interfaces.py so ingestion resilience handling stays
uniform across Tier-1 and Tier-2 sources.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RawNewsItem:
    """Feed-provided metadata only — see module docstring."""

    headline: str
    feed_description: str | None
    publisher: str | None
    source_url: str
    published_at: datetime | None
    raw: dict[str, Any] = field(default_factory=dict)


class NewsSource(ABC):
    """One RSS/feed-based news adapter.

    fetch_all() returns every item the feed currently exposes. LiveMint and
    Economic Times are general feeds (the bake-off found no per-symbol query
    endpoint for either), filtered downstream by
    services/matching/company_matcher.py — company matching never happens
    inside an adapter.
    """

    source_name: str

    @abstractmethod
    async def fetch_all(self) -> list[RawNewsItem]: ...
