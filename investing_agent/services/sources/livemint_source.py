from __future__ import annotations

"""LiveMintNewsSource — production Tier-2 news adapter, Phase 4A.

Backed by LiveMint's general "companies" RSS feed. There is no per-symbol
query endpoint (the bake-off found none) — company relevance is decided
downstream by services/matching/company_matcher.py, never here. RSS
metadata only: headline, feed description, publisher, link, pubDate. Never
the article body — see services/sources/news_interfaces.py.
"""

import httpx

from investing_agent.services.sources.news_interfaces import NewsSource, RawNewsItem
from investing_agent.services.sources.rss_common import DEFAULT_HEADERS, fetch_rss, parse_rss_items

_FEED_URL = "https://www.livemint.com/rss/companies"


class LiveMintNewsSource(NewsSource):
    source_name = "livemint"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=15.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_all(self) -> list[RawNewsItem]:
        body = await fetch_rss(self._client, _FEED_URL)
        if body is None:
            return []
        return parse_rss_items(body)
