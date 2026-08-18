from __future__ import annotations

"""Unit tests for services/sources/rss_common.py — RSS 2.0 parsing and
fetch resilience (retry/backoff, 403 vs transient-error taxonomy).

Network is mocked with httpx.MockTransport (no real requests). Retry/backoff
constants are monkeypatched to near-zero so retry tests stay fast, following
the same pattern as tests/unit/test_nse_source_filings.py.
"""

import httpx
import pytest

from investing_agent.services.sources import rss_common as rss_common_module
from investing_agent.services.sources.interfaces import SourceAccessError, SourceTransientError
from investing_agent.services.sources.rss_common import fetch_rss, parse_rss_items

VALID_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Test Feed</title>
  <item>
    <title>BEL wins Rs 500 crore order from Indian Navy</title>
    <link>https://example.com/bel-order</link>
    <description>Bharat Electronics has won a new defence order.</description>
    <pubDate>Mon, 10 Aug 2026 12:00:00 GMT</pubDate>
    <source url="https://example.com">Example Publisher</source>
  </item>
  <item>
    <title>HAL delivers Tejas aircraft to IAF</title>
    <link>https://example.com/hal-tejas</link>
    <pubDate>Tue, 11 Aug 2026 09:30:00 GMT</pubDate>
  </item>
</channel>
</rss>
"""

EMPTY_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Test Feed</title>
</channel>
</rss>
"""

MALFORMED_RSS = b"<rss version=\"2.0\"><channel><item><title>Broken"

RSS_MISSING_LINK = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <item>
    <title>Headline with no link</title>
  </item>
</channel>
</rss>
"""


def _client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestParseRssItems:
    def test_parses_valid_feed(self) -> None:
        items = parse_rss_items(VALID_RSS)
        assert len(items) == 2
        assert items[0].headline == "BEL wins Rs 500 crore order from Indian Navy"
        assert items[0].source_url == "https://example.com/bel-order"
        assert items[0].feed_description == "Bharat Electronics has won a new defence order."
        assert items[0].publisher == "Example Publisher"
        assert items[0].published_at is not None

    def test_item_without_source_element_has_no_publisher(self) -> None:
        items = parse_rss_items(VALID_RSS)
        assert items[1].publisher is None
        assert items[1].feed_description is None

    def test_malformed_xml_returns_empty_list(self) -> None:
        assert parse_rss_items(MALFORMED_RSS) == []

    def test_empty_feed_returns_empty_list(self) -> None:
        assert parse_rss_items(EMPTY_RSS) == []

    def test_item_missing_link_is_skipped(self) -> None:
        assert parse_rss_items(RSS_MISSING_LINK) == []


class TestFetchRss:
    @pytest.mark.asyncio
    async def test_200_returns_body(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=VALID_RSS)

        client = _client_for(handler)
        body = await fetch_rss(client, "https://example.com/rss")
        assert body == VALID_RSS
        await client.aclose()

    @pytest.mark.asyncio
    async def test_403_raises_source_access_error_without_retry(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(403, content=b"blocked")

        client = _client_for(handler)
        with pytest.raises(SourceAccessError):
            await fetch_rss(client, "https://example.com/rss")
        assert attempts == 1
        await client.aclose()

    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rss_common_module, "_RETRY_WAIT_MIN_SECONDS", 0.001)
        monkeypatch.setattr(rss_common_module, "_RETRY_WAIT_MAX_SECONDS", 0.002)
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                return httpx.Response(503, content=b"unavailable")
            return httpx.Response(200, content=VALID_RSS)

        client = _client_for(handler)
        body = await fetch_rss(client, "https://example.com/rss")
        assert attempts == 3
        assert body == VALID_RSS
        await client.aclose()

    @pytest.mark.asyncio
    async def test_retries_exhausted_raises_source_transient_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(rss_common_module, "_RETRY_ATTEMPTS", 2)
        monkeypatch.setattr(rss_common_module, "_RETRY_WAIT_MIN_SECONDS", 0.001)
        monkeypatch.setattr(rss_common_module, "_RETRY_WAIT_MAX_SECONDS", 0.002)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=b"unavailable")

        client = _client_for(handler)
        with pytest.raises(SourceTransientError):
            await fetch_rss(client, "https://example.com/rss")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_non_retryable_non_200_returns_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, content=b"not found")

        client = _client_for(handler)
        body = await fetch_rss(client, "https://example.com/rss")
        assert body is None
        await client.aclose()

    @pytest.mark.asyncio
    async def test_empty_body_returns_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"")

        client = _client_for(handler)
        body = await fetch_rss(client, "https://example.com/rss")
        assert body is None
        await client.aclose()
