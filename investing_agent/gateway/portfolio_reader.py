from __future__ import annotations

"""Abstract PortfolioReader interface.

PortfolioReader is STRICTLY read-only.  No order placement, no write
operations.  This is the only interface that LangGraph research nodes
may hold a reference to.

Design rule enforced here: any concrete reader that receives write
credentials must raise NotImplementedError for methods outside its scope
rather than silently succeeding.
"""

from abc import ABC, abstractmethod
from typing import Any


class BrokerAuthError(Exception):
    """Access token missing, expired, or invalid."""


class BrokerUnavailableError(Exception):
    """MCP server or broker API is unreachable."""


class BrokerRateLimitError(Exception):
    """Request was rate-limited by the broker."""


class BrokerTimeoutError(Exception):
    """Request timed out."""


class PortfolioReader(ABC):
    """Read-only interface to broker portfolio data.

    Implementations: ZerodhaKiteMCPReader, MockPortfolioReader.

    LangGraph research nodes receive only this interface.
    BrokerExecutor is never passed to research nodes.
    """

    @abstractmethod
    async def get_holdings(self) -> dict[str, Any]:
        """Return equity holdings as raw broker JSON (already validated).

        The caller is responsible for normalising via InstrumentResolver.
        Raises:
            BrokerAuthError: token expired or missing
            BrokerUnavailableError: MCP/API unreachable
            BrokerTimeoutError: request timed out
        """
        ...

    @abstractmethod
    async def get_positions(self) -> dict[str, Any]:
        """Return intraday and net positions.

        Raises same errors as get_holdings.
        """
        ...

    @abstractmethod
    async def get_margins(self) -> dict[str, Any]:
        """Return available margins (equity segment).

        Raises same errors as get_holdings.
        """
        ...

    @abstractmethod
    async def get_quotes(self, instruments: list[str]) -> dict[str, Any]:
        """Return LTP and OHLCV for a list of instruments.

        instruments: list of "EXCHANGE:SYMBOL" strings, e.g. ["NSE:BEL"].
        """
        ...

    @abstractmethod
    async def ping(self) -> bool:
        """Return True if the reader can reach the broker.  Never raises."""
        ...
