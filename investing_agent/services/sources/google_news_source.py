from __future__ import annotations

"""GoogleNewsSource — implemented, but NOT wired into production ingestion.

Explicit user decision (Phase 4 bake-off follow-up): Google News RSS is a
useful discovery source, but licensing/usage restrictions need to be
resolved before automated production ingestion. This adapter exists behind
the NewsSource interface so the option is ready later, but:

  - it is never registered in NewsIngestionService's production source list
    or the sync-news CLI command,
  - its SourceReliability row must be seeded enabled=False,
    research_only=True,
  - no code path currently constructs one outside tests or manual research
    use (e.g. a one-off script).

Unlike LiveMint/ET, Google News IS a query endpoint (search-by-term), so
fetch_query() is the real entry point; fetch_all() deliberately returns []
rather than guessing a default query, since there's no "the" query — a
research script must supply one explicitly.
"""

import urllib.parse

import httpx

from investing_agent.services.sources.news_interfaces import NewsSource, RawNewsItem
from investing_agent.services.sources.rss_common import DEFAULT_HEADERS, fetch_rss, parse_rss_items

_BASE_URL = "https://news.google.com/rss/search"


class GoogleNewsSource(NewsSource):
    source_name = "google_news"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=15.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_query(self, query: str, when: str = "7d") -> list[RawNewsItem]:
        url = (
            f"{_BASE_URL}?q={urllib.parse.quote(query)}+when:{when}"
            f"&hl=en-IN&gl=IN&ceid=IN:en"
        )
        body = await fetch_rss(self._client, url)
        if body is None:
            return []
        return parse_rss_items(body)

    async def fetch_all(self) -> list[RawNewsItem]:
        """No production query is defined for a query-based source — see
        module docstring. Use fetch_query() directly."""
        return []
