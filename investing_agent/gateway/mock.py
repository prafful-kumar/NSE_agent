from __future__ import annotations
"""Mock BrokerGateway for tests and development.

Returns realistic-looking but synthetic Indian equity data.
Does NOT touch any live broker endpoint.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from investing_agent.gateway.base import BrokerExecutionDisabledError, BrokerGateway, OrderRequest, OrderResponse
from investing_agent.schemas.portfolio import BrokerHolding, BrokerPortfolioResponse

# Representative mock portfolio — realistic NSE symbols
_MOCK_HOLDINGS: list[dict[str, Any]] = [
    {
        "tradingsymbol": "RELIANCE",
        "isin": "INE002A01018",
        "exchange": "NSE",
        "quantity": 10,
        "t1_quantity": 0,
        "average_price": Decimal("2450.00"),
        "last_price": Decimal("2680.00"),
        "pnl": Decimal("2300.00"),
        "product": "CNC",
    },
    {
        "tradingsymbol": "HDFCBANK",
        "isin": "INE040A01034",
        "exchange": "NSE",
        "quantity": 20,
        "t1_quantity": 0,
        "average_price": Decimal("1480.00"),
        "last_price": Decimal("1620.00"),
        "pnl": Decimal("2800.00"),
        "product": "CNC",
    },
    {
        "tradingsymbol": "BEL",
        "isin": "INE263A01024",
        "exchange": "NSE",
        "quantity": 200,
        "t1_quantity": 0,
        "average_price": Decimal("180.00"),
        "last_price": Decimal("215.00"),
        "pnl": Decimal("7000.00"),
        "product": "CNC",
    },
    {
        "tradingsymbol": "HAL",
        "isin": "INE066F01012",
        "exchange": "NSE",
        "quantity": 5,
        "t1_quantity": 0,
        "average_price": Decimal("2800.00"),
        "last_price": Decimal("3400.00"),
        "pnl": Decimal("3000.00"),
        "product": "CNC",
    },
]


class MockBrokerGateway(BrokerGateway):
    """Deterministic mock for unit/integration tests.

    BROKER_EXECUTION_ENABLED is always treated as False here;
    place_order always raises BrokerExecutionDisabledError.
    """

    def __init__(self, execution_enabled: bool = False) -> None:
        self._execution_enabled = execution_enabled

    async def get_holdings(self) -> BrokerPortfolioResponse:
        holdings = [BrokerHolding(**h) for h in _MOCK_HOLDINGS]
        return BrokerPortfolioResponse(
            holdings=holdings,
            fetched_at=datetime.now(timezone.utc),
            source="mock_broker",
        )

    async def get_quote(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        prices: dict[str, Any] = {
            "RELIANCE": {"last_price": 2680.0, "change_pct": 1.2},
            "HDFCBANK": {"last_price": 1620.0, "change_pct": -0.4},
            "BEL": {"last_price": 215.0, "change_pct": 2.1},
            "HAL": {"last_price": 3400.0, "change_pct": 0.8},
        }
        return {sym: prices.get(sym, {"last_price": None, "change_pct": None}) for sym in symbols}

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        self._assert_execution_enabled()
        # If somehow execution_enabled is True in tests, return a mock response
        return OrderResponse(
            order_id="MOCK-ORDER-001",
            status="MOCK_SUBMITTED",
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
            submitted_at=datetime.now(timezone.utc),
            raw_response={"mock": True},
        )
