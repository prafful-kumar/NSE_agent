from __future__ import annotations

"""Shared RSS fetch + parse helpers for Phase 4A NewsSource adapters.

Resilience mirrors nse_source.py::_get_resilient: transient failures
(network errors, 429/5xx) are retried with exponential backoff via
tenacity; an HTTP 403 is raised as SourceAccessError and never retried. A
malformed or empty feed body is reported as "no items" (None/[]), never
guessed around or raised as an error — a broken feed is a data-quality
signal for SourceReliability.stale_feed_count, not a crash.
"""

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
import structlog
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from investing_agent.services.sources.interfaces import SourceAccessError, SourceTransientError
from investing_agent.services.sources.news_interfaces import RawNewsItem

log = structlog.get_logger(__name__)

DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; investing-agent/1.0; +research use)"}
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_RETRY_ATTEMPTS = 3
_RETRY_WAIT_MIN_SECONDS = 2.0
_RETRY_WAIT_MAX_SECONDS = 10.0


async def fetch_rss(client: httpx.AsyncClient, url: str) -> bytes | None:
    """Returns the raw feed body, or None if the source served a
    non-retryable non-200/empty response. Raises SourceAccessError on 403,
    SourceTransientError if retries are exhausted on a transient failure."""
    async for attempt in AsyncRetrying(
        retry=retry_if_exception_type(SourceTransientError),
        stop=stop_after_attempt(_RETRY_ATTEMPTS),
        wait=wait_exponential(
            multiplier=1, min=_RETRY_WAIT_MIN_SECONDS, max=_RETRY_WAIT_MAX_SECONDS
        ),
        reraise=True,
    ):
        with attempt:
            try:
                resp = await client.get(url)
            except httpx.TransportError as exc:
                raise SourceTransientError(f"network error fetching {url}: {exc}") from exc
            log.info("rss_common.request", url=url, status=resp.status_code)
            if resp.status_code == 403:
                raise SourceAccessError(
                    f"{url} returned 403 (likely anti-bot block) — not retrying"
                )
            if resp.status_code in _RETRYABLE_STATUSES:
                raise SourceTransientError(f"{url} returned {resp.status_code}")
            if resp.status_code != 200 or not resp.content:
                return None
            return resp.content
    raise AssertionError("unreachable — AsyncRetrying always returns or raises")


def parse_rss_items(body: bytes) -> list[RawNewsItem]:
    """Deterministic RSS 2.0 <item> parsing. Items missing a title or link
    are skipped (source_url is required downstream); malformed XML yields
    an empty list rather than raising."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []

    items: list[RawNewsItem] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        description = (item.findtext("description") or "").strip() or None
        pub_raw = item.findtext("pubDate")
        source_el = item.find("source")
        publisher = source_el.text.strip() if source_el is not None and source_el.text else None
        items.append(
            RawNewsItem(
                headline=title,
                feed_description=description,
                publisher=publisher,
                source_url=link,
                published_at=_parse_pubdate(pub_raw),
                raw={"pub_date_raw": pub_raw},
            )
        )
    return items


def _parse_pubdate(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (TypeError, ValueError):
        return None
