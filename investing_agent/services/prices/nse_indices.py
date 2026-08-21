from __future__ import annotations

"""NSEIndicesBenchmarkPriceProvider -- official NSE Indices historical
close-all data (Phase 6C.0).

Uses NSE's own "index bhavcopy" (ind_close_all_DDMMYYYY.csv), one file per
trading_date covering every NSE index's OHLC for that day -- a static
archived download, not the undocumented nseindia.com/api/* JSON surface.
Same host (archives.nseindia.com) and stability profile as the legacy
equity bhavcopy; confirmed live during the capability spike.

Starts with NIFTY 50 (the price index). NIFTY 50 TRI is not present in this
archive; it is fetched from Nifty Indices' separately audited total-return
history endpoint (see ``nifty_tri.py``).
"""

from datetime import date, datetime

import httpx
import structlog

from investing_agent.services.prices.interfaces import (
    BenchmarkPriceProvider,
    ParsedBenchmarkPrice,
    RawPriceFile,
)
from investing_agent.services.prices.parsers import parse_index_close_all
from investing_agent.services.sources.interfaces import SourceAccessError, SourceTransientError

log = structlog.get_logger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; investing-agent/1.0; +research use)"}

_INDEX_CLOSE_ALL_URL_TEMPLATE = (
    "https://archives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv"
)


class NSEIndicesBenchmarkPriceProvider(BenchmarkPriceProvider):
    def __init__(self, benchmark_code: str, client: httpx.AsyncClient | None = None) -> None:
        self._benchmark_code = benchmark_code
        self._client = client or httpx.AsyncClient(headers=_HEADERS, timeout=30.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    def source_format(self, trading_date: date) -> str:
        return "NSE_INDICES_CLOSE_ALL"

    def _url_for(self, trading_date: date) -> str:
        return _INDEX_CLOSE_ALL_URL_TEMPLATE.format(ddmmyyyy=trading_date.strftime("%d%m%Y"))

    async def fetch_raw(self, trading_date: date) -> RawPriceFile | None:
        url = self._url_for(trading_date)
        try:
            resp = await self._client.get(url)
        except httpx.TransportError as exc:
            raise SourceTransientError(f"network error fetching {url}: {exc}") from exc

        log.info("nse_indices.request", url=url, status=resp.status_code)
        if resp.status_code == 404:
            return None  # weekend/holiday
        if resp.status_code == 403:
            raise SourceAccessError(f"NSE returned 403 for {url} (likely anti-bot block)")
        if resp.status_code in (429, 500, 502, 503, 504):
            raise SourceTransientError(f"NSE returned {resp.status_code} for {url}")
        if resp.status_code != 200 or not resp.content:
            return None

        return RawPriceFile(
            content=resp.content,
            source_url=url,
            content_type=resp.headers.get("content-type"),
            fetched_at=datetime.now(tz=None).astimezone(),
        )

    def parse(self, raw: RawPriceFile, trading_date: date) -> list[ParsedBenchmarkPrice]:
        return parse_index_close_all(raw.content, self._benchmark_code)
