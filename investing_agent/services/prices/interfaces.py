from __future__ import annotations

"""Historical market price provider interfaces (Phase 6C.0).

Two ABCs, mirroring the split between equity and index data: NSE bhavcopy
and NSE Indices close-all are two genuinely different file formats/URLs,
not just two symbols on the same feed. fetch_raw returns None (not an
exception) for a trading_date with no file -- a weekend/holiday is
expected, routine input, never an error.

This is deliberately a separate, narrower interface from
investing_agent/services/reconstruction/price_provider.py's
HistoricalPriceProvider Protocol, which is a point-in-time *query*
interface for Phase 6B mark-to-market (get_close(symbol, isin, as_of) ->
Decimal | None, reading from wherever prices already live). The provider
here is the *ingestion*-side abstraction: it fetches a whole day's raw
file and parses it into rows to be persisted. Wiring daily_prices as a
backing store for that query Protocol is a natural follow-up, not done in
this task.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class RawPriceFile:
    """Raw bytes fetched for one trading_date, not yet archived/parsed."""

    content: bytes
    source_url: str
    content_type: str | None
    fetched_at: datetime


@dataclass(frozen=True)
class ParsedDailyPrice:
    symbol: str
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None


@dataclass(frozen=True)
class ParsedBenchmarkPrice:
    benchmark_code: str
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


class HistoricalPriceProvider(ABC):
    """Fetches + parses whole-market equity bhavcopy files, one trading_date
    at a time. `source_format` identifies which of the (possibly several)
    normalized parsers produced a given ParsedDailyPrice list, for
    provenance."""

    @abstractmethod
    async def fetch_raw(self, trading_date: date) -> RawPriceFile | None:
        """None means "no file for this date" (weekend/holiday) -- never a
        guess, never an exception for the routine case."""

    @abstractmethod
    def parse(self, raw: RawPriceFile, trading_date: date) -> list[ParsedDailyPrice]:
        """Pure, deterministic. No LLM, no network, no adjustment inference."""

    @abstractmethod
    def source_format(self, trading_date: date) -> str:
        """Which normalized format applies to this trading_date (used for
        provenance on PriceArchiveFile, decided before any network call)."""


class BenchmarkPriceProvider(ABC):
    @abstractmethod
    async def fetch_raw(self, trading_date: date) -> RawPriceFile | None:
        """None means "no file for this date" (weekend/holiday)."""

    @abstractmethod
    def parse(self, raw: RawPriceFile, trading_date: date) -> list[ParsedBenchmarkPrice]:
        """Pure, deterministic."""

    @abstractmethod
    def source_format(self, trading_date: date) -> str:
        ...
