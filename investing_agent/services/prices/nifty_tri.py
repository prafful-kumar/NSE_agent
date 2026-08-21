from __future__ import annotations

"""Official Nifty Indices NIFTY 50 total-return history provider.

The NSE ``ind_close_all`` archive does not publish NIFTY 50 TRI.  Nifty
Indices' historical-data page instead exposes this documented client request:
``POST /BackPage/getTotalReturnIndexString``.  Its response is archived
verbatim before the terminal TRI value is persisted.
"""

import json
from datetime import date, datetime
from decimal import Decimal

import httpx
import structlog

from investing_agent.services.prices.interfaces import (
    BenchmarkPriceProvider,
    ParsedBenchmarkPrice,
    RawPriceFile,
)
from investing_agent.services.sources.interfaces import SourceAccessError, SourceTransientError

log = structlog.get_logger(__name__)

_HISTORY_URL = "https://www.niftyindices.com/BackPage/getTotalReturnIndexString"
_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://www.niftyindices.com",
    "Referer": "https://www.niftyindices.com/reports/historical-data",
    "User-Agent": "Mozilla/5.0 (compatible; investing-agent/1.0; +research use)",
}


class NiftyIndicesTRIBenchmarkPriceProvider(BenchmarkPriceProvider):
    """Fetch NIFTY 50's official daily total-return index level.

    The endpoint supplies only the terminal index level, not OHLC.  The
    normalized benchmark table requires OHLC, so all four values are set to
    that one published daily level; analytics use ``close`` exclusively.
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(headers=_HEADERS, timeout=45.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    def source_format(self, trading_date: date) -> str:
        return "NIFTY_INDICES_TOTAL_RETURN"

    async def fetch_raw(self, trading_date: date) -> RawPriceFile | None:
        return await self.fetch_range(trading_date, trading_date)

    async def fetch_range(self, start_date: date, end_date: date) -> RawPriceFile | None:
        """Fetch up to one year of daily TRI levels in one official request."""
        if end_date < start_date:
            raise ValueError("end_date must not precede start_date")
        if (end_date - start_date).days > 365:
            raise ValueError("Nifty Indices TRI requests are limited to one year")
        try:
            response = await self._client.post(
                _HISTORY_URL,
                json=self._payload_for_range(start_date, end_date),
            )
        except httpx.TransportError as exc:
            raise SourceTransientError(f"network error fetching NIFTY 50 TRI: {exc}") from exc

        log.info(
            "nifty_tri.request",
            start_date=str(start_date),
            end_date=str(end_date),
            status=response.status_code,
        )
        if response.status_code in (401, 403):
            raise SourceAccessError(f"Nifty Indices returned {response.status_code} for TRI history")
        if response.status_code in (429, 500, 502, 503, 504):
            raise SourceTransientError(f"Nifty Indices returned {response.status_code} for TRI history")
        if response.status_code != 200 or not response.content:
            return None
        return RawPriceFile(
            content=response.content,
            source_url=(
                f"{_HISTORY_URL}?index=NIFTY%2050&start_date={start_date}&end_date={end_date}"
            ),
            content_type=response.headers.get("content-type"),
            fetched_at=datetime.now(tz=None).astimezone(),
        )

    def parse(self, raw: RawPriceFile, trading_date: date) -> list[ParsedBenchmarkPrice]:
        return [row for row in self.parse_range(raw) if row.trading_date == trading_date]

    def parse_range(self, raw: RawPriceFile) -> list[ParsedBenchmarkPrice]:
        payload = json.loads(raw.content.decode("utf-8-sig"))
        if not isinstance(payload, list):
            raise ValueError("Nifty Indices TRI response must be a list")
        rows: list[ParsedBenchmarkPrice] = []
        for item in payload:
            if not isinstance(item, dict) or item.get("Index Name", "").casefold() != "nifty 50":
                continue
            observed_date = datetime.strptime(str(item["Date"]), "%d %b %Y").date()
            value = Decimal(str(item["TotalReturnsIndex"]))
            rows.append(
                ParsedBenchmarkPrice(
                    benchmark_code="NIFTY_50_TRI",
                    trading_date=observed_date,
                    open=value,
                    high=value,
                    low=value,
                    close=value,
                )
            )
        return rows

    def _payload_for_range(self, start_date: date, end_date: date) -> dict[str, str]:
        start, end = start_date.isoformat(), end_date.isoformat()
        return {
            "cinfo": (
                "{'name':'NIFTY 50','startDate':'"
                f"{start}','endDate':'{end}','indexName':'NIFTY 50'}}"
            )
        }
