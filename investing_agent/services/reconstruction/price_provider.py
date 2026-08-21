from __future__ import annotations

"""Historical price lookup for mark-to-market (Phase 6B).

No historical daily-close price service exists anywhere in this codebase
today -- the only price-related code (gateway/zerodha_reader.py,
gateway/portfolio_reader.py) fetches live LTP, not a point-in-time
historical close. Rather than fabricate or interpolate a price, unrealized
P&L is simply left unset (None, price_source="UNAVAILABLE") whenever no
provider is given or a provider has no data for that symbol/date.

StatementDerivedPriceProvider is a deliberate stopgap: it reads the
"Previous Closing Price" values already present in a Zerodha P&L / holdings
export (data already sitting in statements/, zero new integration) and can
answer price queries only for the small set of discrete dates each export
was generated on -- it is NOT a general arbitrary-date price source. A real
historical daily-close feed (e.g. NSE bhavcopy ingestion) is a separate,
later piece of work.
"""

from datetime import date
from decimal import Decimal
from typing import Protocol


class HistoricalPriceProvider(Protocol):
    def get_close(self, symbol: str, isin: str | None, as_of: date) -> Decimal | None:
        """Best-effort point-in-time price. None means "no data", never a
        guess."""
        ...


class StatementDerivedPriceProvider:
    """Prices keyed by (symbol, exact export date) -- built from a Zerodha
    P&L or holdings export's own "Previous Closing Price" column. See
    pnl_report_parser.py for how these are extracted."""

    def __init__(self, prices_by_symbol_and_date: dict[tuple[str, date], Decimal]) -> None:
        self._prices = prices_by_symbol_and_date

    def get_close(self, symbol: str, isin: str | None, as_of: date) -> Decimal | None:
        return self._prices.get((symbol, as_of))
