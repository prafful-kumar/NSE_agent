from __future__ import annotations

"""EconomicTimesNewsSource — production Tier-2 news adapter, Phase 4A.

Backed by ET's general markets/stocks RSS feed. No per-symbol query
endpoint (the bake-off found none) — company relevance is decided
downstream by services/matching/company_matcher.py, never here. RSS
metadata only — see services/sources/news_interfaces.py.
"""

import httpx

from investing_agent.services.sources.news_interfaces import NewsSource, RawNewsItem
from investing_agent.services.sources.rss_common import DEFAULT_HEADERS, fetch_rss, parse_rss_items

_FEED_URL = "https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms"


class EconomicTimesNewsSource(NewsSource):
    source_name = "economic_times"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=15.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_all(self) -> list[RawNewsItem]:
        body = await fetch_rss(self._client, _FEED_URL)
        if body is None:
            return []
        return parse_rss_items(body)
