from __future__ import annotations

from investing_agent.gateway.base import (
    BrokerGateway,
    BrokerExecutionDisabledError,
)
from investing_agent.gateway.broker_executor import (
    BrokerExecutor,
    ExecutionDisabledError,
    MockBrokerExecutor,
    OrderRequest,
    OrderResponse,
)
from investing_agent.gateway.mock import MockBrokerGateway
from investing_agent.gateway.mock_reader import MockPortfolioReader
from investing_agent.gateway.portfolio_reader import (
    BrokerAuthError,
    BrokerRateLimitError,
    BrokerTimeoutError,
    BrokerUnavailableError,
    PortfolioReader,
)

__all__ = [
    # Phase 1 (kept for backward compat)
    "BrokerGateway",
    "BrokerExecutionDisabledError",
    "MockBrokerGateway",
    # Phase 2
    "PortfolioReader",
    "MockPortfolioReader",
    "BrokerExecutor",
    "MockBrokerExecutor",
    "OrderRequest",
    "OrderResponse",
    # Errors
    "BrokerAuthError",
    "BrokerUnavailableError",
    "BrokerRateLimitError",
    "BrokerTimeoutError",
    "ExecutionDisabledError",
]
