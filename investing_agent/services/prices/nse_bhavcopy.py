from __future__ import annotations

"""NSEBhavcopyHistoricalPriceProvider -- official NSE daily cash-market
bhavcopy (Phase 6C.0).

Two URL patterns exist depending on trading_date, confirmed live during the
capability spike (research/provider_evaluation/):

- >= 2024-07-08 (UDIFF_FORMAT_START_DATE): the current "CM-UDiFF Common
  Bhavcopy Final" zip, hosted at nsearchives.nseindia.com/content/cm/.
  Verified retroactively unavailable before this date (404).
- <  2024-07-08: the classic bhavcopy zip, hosted at
  archives.nseindia.com/content/historical/EQUITIES/. Verified this legacy
  URL 404s from 2024-07-08 onward -- there is no gap and no ambiguous
  overlap window in practice (both existed briefly through 2024-07-05, the
  last trading day before the cutover).

fetch_raw returns None (not an exception) on HTTP 404, since the single
most common reason a bhavcopy file is missing for a given calendar date is
a weekend or trading holiday -- routine, expected input, never an error.
"""

from datetime import date, datetime

import httpx
import structlog

from investing_agent.services.prices.interfaces import (
    HistoricalPriceProvider,
    ParsedDailyPrice,
    RawPriceFile,
)
from investing_agent.services.prices.parsers import (
    parse_legacy_equity_bhavcopy,
    parse_udiff_equity_bhavcopy,
)
from investing_agent.services.sources.interfaces import SourceAccessError, SourceTransientError

log = structlog.get_logger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; investing-agent/1.0; +research use)"}

UDIFF_FORMAT_START_DATE = date(2024, 7, 8)

_UDIFF_URL_TEMPLATE = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)
_LEGACY_URL_TEMPLATE = (
    "https://archives.nseindia.com/content/historical/EQUITIES/"
    "{year}/{mon}/cm{ddmonyyyy}bhav.csv.zip"
)


def _udiff_url(trading_date: date) -> str:
    return _UDIFF_URL_TEMPLATE.format(yyyymmdd=trading_date.strftime("%Y%m%d"))


def _legacy_url(trading_date: date) -> str:
    return _LEGACY_URL_TEMPLATE.format(
        year=trading_date.year,
        mon=trading_date.strftime("%b").upper(),
        ddmonyyyy=trading_date.strftime("%d%b%Y").upper(),
    )


class NSEBhavcopyHistoricalPriceProvider(HistoricalPriceProvider):
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(headers=_HEADERS, timeout=30.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    def source_format(self, trading_date: date) -> str:
        return "NSE_CM_UDIFF" if trading_date >= UDIFF_FORMAT_START_DATE else "NSE_CM_LEGACY"

    def _url_for(self, trading_date: date) -> str:
        return (
            _udiff_url(trading_date)
            if trading_date >= UDIFF_FORMAT_START_DATE
            else _legacy_url(trading_date)
        )

    async def fetch_raw(self, trading_date: date) -> RawPriceFile | None:
        url = self._url_for(trading_date)
        try:
            resp = await self._client.get(url)
        except httpx.TransportError as exc:
            raise SourceTransientError(f"network error fetching {url}: {exc}") from exc

        log.info("nse_bhavcopy.request", url=url, status=resp.status_code)
        if resp.status_code == 404:
            return None  # weekend/holiday -- routine, not an error
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

    def parse(self, raw: RawPriceFile, trading_date: date) -> list[ParsedDailyPrice]:
        if self.source_format(trading_date) == "NSE_CM_UDIFF":
            return parse_udiff_equity_bhavcopy(raw.content)
        return parse_legacy_equity_bhavcopy(raw.content)
