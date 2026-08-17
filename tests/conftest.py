"""Pytest configuration and shared fixtures."""

import os
import pytest

# Use a test-specific settings by setting env vars before any imports
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://investing:investing@localhost:5433/investing_agent_test")
os.environ.setdefault("BROKER_EXECUTION_ENABLED", "false")
os.environ.setdefault("APP_ENV", "development")


@pytest.fixture
def mock_broker():
    from investing_agent.gateway.mock import MockBrokerGateway
    return MockBrokerGateway(execution_enabled=False)


@pytest.fixture
def mock_broker_execution_enabled():
    """Broker with execution enabled — only for testing the guard itself."""
    from investing_agent.gateway.mock import MockBrokerGateway
    return MockBrokerGateway(execution_enabled=True)
