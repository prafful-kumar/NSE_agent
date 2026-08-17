from __future__ import annotations

"""Abstract BrokerExecutor interface.

BrokerExecutor is the write side of the broker integration.
It is NEVER injected into LangGraph research nodes.
It may only be called from the Approval node after:
  1. A human approval interrupt has fired.
  2. All deterministic risk checks have passed.
  3. BROKER_EXECUTION_ENABLED=true in settings.

Phase 2: this interface is defined but no concrete implementation
beyond MockBrokerExecutor is provided. Live execution comes in Phase 8.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


class ExecutionDisabledError(RuntimeError):
    """Raised when any write operation is attempted and execution is disabled."""


class DuplicateOrderError(RuntimeError):
    """Raised when the same order would be submitted twice."""


@dataclass
class OrderRequest:
    symbol: str
    exchange: str
    side: str          # BUY | SELL
    quantity: int
    order_type: str    # MARKET | LIMIT | SL | SL-M
    price: Decimal | None
    product: str       # CNC | MIS
    validity: str = "DAY"
    tag: str | None = None
    recommendation_id: str | None = None


@dataclass
class OrderResponse:
    order_id: str
    status: str
    symbol: str
    side: str
    quantity: int
    price: Decimal | None
    submitted_at: datetime
    raw_response: dict[str, Any]


class BrokerExecutor(ABC):
    """Write operations.  Always behind execution guard + human approval."""

    def __init__(self, execution_enabled: bool = False) -> None:
        self._execution_enabled = execution_enabled

    def _assert_enabled(self) -> None:
        if not self._execution_enabled:
            raise ExecutionDisabledError(
                "Broker execution is disabled. "
                "BROKER_EXECUTION_ENABLED must be true AND a human approval "
                "interrupt must have fired before calling this method."
            )

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResponse: ...

    @abstractmethod
    async def modify_order(
        self, order_id: str, quantity: int | None, price: Decimal | None
    ) -> OrderResponse: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...


class MockBrokerExecutor(BrokerExecutor):
    """Always-disabled executor for tests."""

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        self._assert_enabled()
        return OrderResponse(
            order_id="MOCK-EXEC-001",
            status="MOCK_SUBMITTED",
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
            submitted_at=datetime.utcnow(),
            raw_response={"mock": True},
        )

    async def modify_order(
        self, order_id: str, quantity: int | None, price: Decimal | None
    ) -> OrderResponse:
        self._assert_enabled()
        raise NotImplementedError

    async def cancel_order(self, order_id: str) -> bool:
        self._assert_enabled()
        return True
