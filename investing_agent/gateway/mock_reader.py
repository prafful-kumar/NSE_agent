from __future__ import annotations

"""MockPortfolioReader — scenario-driven test double.

Supports multiple scenarios via the 'scenario' constructor parameter:
  - "normal"          : 4 realistic holdings
  - "empty"           : no holdings
  - "with_etf"        : mix of EQ and ETF holdings
  - "missing_price"   : one holding has last_price = 0
  - "auth_expired"    : raises BrokerAuthError
  - "unavailable"     : raises BrokerUnavailableError
  - "timeout"         : raises BrokerTimeoutError
  - "malformed"       : returns invalid/incomplete data to test normalisation

All scenarios can also be loaded from JSON fixtures in tests/fixtures/zerodha/.
"""

import json
from pathlib import Path
from typing import Any

from investing_agent.gateway.portfolio_reader import (
    BrokerAuthError,
    BrokerTimeoutError,
    BrokerUnavailableError,
    PortfolioReader,
)

_FIXTURE_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "zerodha"

# ── Built-in scenario data ─────────────────────────────────────────────────────

_SCENARIOS: dict[str, dict[str, Any]] = {
    "normal": {
        "holdings": [
            {
                "tradingsymbol": "RELIANCE",
                "exchange": "NSE",
                "isin": "INE002A01018",
                "quantity": 10,
                "t1_quantity": 0,
                "realised_quantity": 10,
                "average_price": 2450.0,
                "last_price": 2680.0,
                "close_price": 2660.0,
                "pnl": 2300.0,
                "day_change": 20.0,
                "day_change_percentage": 0.75,
                "product": "CNC",
                "instrument_type": "EQ",
            },
            {
                "tradingsymbol": "HDFCBANK",
                "exchange": "NSE",
                "isin": "INE040A01034",
                "quantity": 20,
                "t1_quantity": 0,
                "realised_quantity": 20,
                "average_price": 1480.0,
                "last_price": 1620.0,
                "close_price": 1610.0,
                "pnl": 2800.0,
                "day_change": 10.0,
                "day_change_percentage": 0.62,
                "product": "CNC",
                "instrument_type": "EQ",
            },
            {
                "tradingsymbol": "BEL",
                "exchange": "NSE",
                "isin": "INE263A01024",
                "quantity": 200,
                "t1_quantity": 0,
                "realised_quantity": 200,
                "average_price": 180.0,
                "last_price": 215.0,
                "close_price": 210.0,
                "pnl": 7000.0,
                "day_change": 5.0,
                "day_change_percentage": 2.38,
                "product": "CNC",
                "instrument_type": "EQ",
            },
            {
                "tradingsymbol": "HAL",
                "exchange": "NSE",
                "isin": "INE066F01012",
                "quantity": 5,
                "t1_quantity": 0,
                "realised_quantity": 5,
                "average_price": 2800.0,
                "last_price": 3400.0,
                "close_price": 3380.0,
                "pnl": 3000.0,
                "day_change": 20.0,
                "day_change_percentage": 0.59,
                "product": "CNC",
                "instrument_type": "EQ",
            },
        ]
    },
    "empty": {"holdings": []},
    "with_etf": {
        "holdings": [
            {
                "tradingsymbol": "RELIANCE",
                "exchange": "NSE",
                "isin": "INE002A01018",
                "quantity": 10,
                "t1_quantity": 0,
                "realised_quantity": 10,
                "average_price": 2450.0,
                "last_price": 2680.0,
                "close_price": 2660.0,
                "pnl": 2300.0,
                "day_change": 20.0,
                "day_change_percentage": 0.75,
                "product": "CNC",
                "instrument_type": "EQ",
            },
            {
                "tradingsymbol": "NIFTYBEES",
                "exchange": "NSE",
                "isin": "INF204KB12I2",
                "quantity": 100,
                "t1_quantity": 0,
                "realised_quantity": 100,
                "average_price": 240.0,
                "last_price": 260.0,
                "close_price": 258.0,
                "pnl": 2000.0,
                "day_change": 2.0,
                "day_change_percentage": 0.78,
                "product": "CNC",
                "instrument_type": "ETF",
            },
        ]
    },
    "missing_price": {
        "holdings": [
            {
                "tradingsymbol": "SOMECO",
                "exchange": "NSE",
                "isin": "INE999X99999",
                "quantity": 10,
                "t1_quantity": 0,
                "realised_quantity": 10,
                "average_price": 100.0,
                "last_price": 0.0,   # price unavailable / circuit
                "close_price": 0.0,
                "pnl": 0.0,
                "day_change": 0.0,
                "day_change_percentage": 0.0,
                "product": "CNC",
                "instrument_type": "EQ",
            },
        ]
    },
    "malformed": {
        # Missing required fields — normalisation must handle gracefully
        "holdings": [
            {"tradingsymbol": "PARTIAL", "exchange": "NSE"},
        ]
    },
}

_POSITIONS_DEFAULT: dict[str, Any] = {
    "day": [],
    "net": [
        {
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "product": "CNC",
            "quantity": 10,
            "buy_quantity": 10,
            "sell_quantity": 0,
            "buy_price": 2450.0,
            "sell_price": 0.0,
            "last_price": 2680.0,
            "pnl": 2300.0,
            "realised": 0.0,
            "unrealised": 2300.0,
        }
    ],
}

_MARGINS_DEFAULT: dict[str, Any] = {
    "equity": {
        "enabled": True,
        "net": 250000.0,
        "available": {
            "adhoc_margin": 0.0,
            "cash": 250000.0,
            "collateral": 0.0,
            "intraday_payin": 0.0,
        },
        "utilised": {
            "holding_sales": 0.0,
            "exposure": 0.0,
            "span": 0.0,
        },
    }
}


class MockPortfolioReader(PortfolioReader):
    """Deterministic test double for PortfolioReader.

    Pass scenario="auth_expired" etc. to simulate error conditions.
    Pass fixture_file="holdings_normal.json" to load from tests/fixtures/.
    """

    def __init__(
        self,
        scenario: str = "normal",
        fixture_file: str | None = None,
    ) -> None:
        self._scenario = scenario
        self._fixture_file = fixture_file

    def _load_data(self) -> dict[str, Any]:
        if self._fixture_file:
            path = _FIXTURE_DIR / self._fixture_file
            with open(path) as f:
                return json.load(f)
        return _SCENARIOS.get(self._scenario, _SCENARIOS["normal"])

    def _check_error_scenario(self) -> None:
        if self._scenario == "auth_expired":
            raise BrokerAuthError(
                "Access token expired. Generate a new token from kite.zerodha.com."
            )
        if self._scenario == "unavailable":
            raise BrokerUnavailableError("Kite MCP is unreachable (simulated).")
        if self._scenario == "timeout":
            raise BrokerTimeoutError("Request timed out after 10s (simulated).")

    async def get_holdings(self) -> dict[str, Any]:
        self._check_error_scenario()
        return self._load_data()

    async def get_positions(self) -> dict[str, Any]:
        self._check_error_scenario()
        return _POSITIONS_DEFAULT

    async def get_margins(self) -> dict[str, Any]:
        self._check_error_scenario()
        return _MARGINS_DEFAULT

    async def get_quotes(self, instruments: list[str]) -> dict[str, Any]:
        self._check_error_scenario()
        base: dict[str, Any] = {}
        price_map = {
            "NSE:RELIANCE": 2680.0,
            "NSE:HDFCBANK": 1620.0,
            "NSE:BEL": 215.0,
            "NSE:HAL": 3400.0,
        }
        for inst in instruments:
            price = price_map.get(inst, 100.0)
            base[inst] = {
                "last_price": price,
                "ohlc": {"open": price * 0.99, "high": price * 1.01,
                          "low": price * 0.98, "close": price},
                "volume": 1_000_000,
            }
        return base

    async def ping(self) -> bool:
        return self._scenario not in ("unavailable", "timeout")
