from __future__ import annotations
"""Zerodha Kite MCP gateway.

Connects to the official Kite MCP server via langchain-mcp-adapters.
All broker write operations are gated by BROKER_EXECUTION_ENABLED.

Phase 1: skeleton with connection setup.
Phase 2: full holdings/quote implementation.
Phase 8: order placement (remains disabled by default).

References:
  https://zerodha.com/products/mcp/
  https://kite.trade/docs/connect/v3/
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from investing_agent.config.settings import Settings
from investing_agent.gateway.base import (
    BrokerAuthError,
    BrokerExecutionDisabledError,
    BrokerGateway,
    OrderRequest,
    OrderResponse,
)
from investing_agent.schemas.portfolio import BrokerHolding, BrokerPortfolioResponse

try:
    from langchain_mcp_adapters.client import MultiServerMCPClient
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False


class ZerodhaKiteMCPGateway(BrokerGateway):
    """Zerodha Kite MCP implementation.

    The MCP server exposes tools like:
      - kite_holdings
      - kite_positions
      - kite_quote
      - kite_place_order  (only when execution is enabled)

    This gateway wraps those tools and enforces safety constraints.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._execution_enabled = settings.broker_execution_enabled
        self._mcp_url = settings.zerodha_mcp_url

        if not _MCP_AVAILABLE:
            raise ImportError(
                "langchain-mcp-adapters is required for ZerodhaKiteMCPGateway. "
                "Install it: pip install langchain-mcp-adapters"
            )

        if not settings.zerodha_access_token:
            raise BrokerAuthError(
                "ZERODHA_ACCESS_TOKEN is not set. "
                "Generate a daily access token from https://kite.zerodha.com"
            )

    def _get_mcp_config(self) -> dict[str, Any]:
        return {
            "kite": {
                "transport": "streamable_http",
                "url": self._mcp_url,
                "headers": {
                    "Authorization": f"Bearer {self._settings.zerodha_access_token.get_secret_value()}"  # type: ignore[union-attr]
                },
            }
        }

    async def get_holdings(self) -> BrokerPortfolioResponse:
        """Fetch equity holdings from Kite MCP."""
        # Phase 2 will implement real MCP calls.
        # The MCP tool name for Zerodha holdings is `kite_holdings`.
        raise NotImplementedError(
            "ZerodhaKiteMCPGateway.get_holdings() is not yet implemented. "
            "Use MockBrokerGateway for development."
        )

    async def get_quote(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        raise NotImplementedError(
            "ZerodhaKiteMCPGateway.get_quote() is not yet implemented."
        )

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        """Place a live order through Kite MCP.

        WILL NOT execute unless:
        1. BROKER_EXECUTION_ENABLED=true in settings
        2. Called from the Approval node after human approval interrupt
        3. All deterministic risk checks passed
        """
        self._assert_execution_enabled()

        # Phase 8 will implement real order placement.
        raise NotImplementedError("Order placement not yet implemented.")
