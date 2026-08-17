from __future__ import annotations
"""Abstract BrokerGateway.

ALL broker interactions go through this interface.
Write operations are permanently gated by BROKER_EXECUTION_ENABLED.

Design rule: research nodes must NEVER have a reference to a
concrete broker gateway that allows writes.  Only the Approval node
receives a write-capable gateway, and only after human approval.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from investing_agent.schemas.portfolio import BrokerHolding, BrokerPortfolioResponse


class BrokerExecutionDisabledError(RuntimeError):
    """Raised when a write operation is attempted while execution is disabled."""


class BrokerAuthError(RuntimeError):
    """Raised when broker credentials are missing or invalid."""


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
    # The recommendation ID that triggered this order (audit link)
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


class BrokerGateway(ABC):
    """Abstract interface for all broker operations.

    Concrete implementations: ZerodhaKiteMCPGateway, MockBrokerGateway.
    The gateway must be constructed with the settings object so that
    execution-enable state is enforced at the instance level.
    """

    @abstractmethod
    async def get_holdings(self) -> BrokerPortfolioResponse:
        """Return all equity holdings. Read-only."""
        ...

    @abstractmethod
    async def get_quote(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """Return current quotes for a list of symbols. Read-only."""
        ...

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResponse:
        """Place a live order.

        MUST raise BrokerExecutionDisabledError when
        BROKER_EXECUTION_ENABLED is false.
        MUST only be called after a human approval interrupt.
        """
        ...

    def _assert_execution_enabled(self) -> None:
        """Call this at the top of place_order()."""
        if not self._execution_enabled:  # type: ignore[attr-defined]
            raise BrokerExecutionDisabledError(
                "Broker execution is disabled. "
                "Set BROKER_EXECUTION_ENABLED=true only after the "
                "human-approval workflow has been fully tested."
            )
