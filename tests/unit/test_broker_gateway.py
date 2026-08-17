"""Tests for BrokerGateway contract and safety guarantees."""

import pytest
from decimal import Decimal

from investing_agent.gateway.base import BrokerExecutionDisabledError, OrderRequest
from investing_agent.gateway.mock import MockBrokerGateway


class TestBrokerExecutionGuard:
    async def test_place_order_raises_when_disabled(self, mock_broker):
        order = OrderRequest(
            symbol="BEL",
            exchange="NSE",
            side="BUY",
            quantity=100,
            order_type="LIMIT",
            price=Decimal("210.00"),
            product="CNC",
        )
        with pytest.raises(BrokerExecutionDisabledError):
            await mock_broker.place_order(order)

    async def test_place_order_succeeds_when_enabled(self, mock_broker_execution_enabled):
        """When execution is explicitly enabled, order goes through (mock response)."""
        order = OrderRequest(
            symbol="BEL",
            exchange="NSE",
            side="BUY",
            quantity=100,
            order_type="LIMIT",
            price=Decimal("210.00"),
            product="CNC",
        )
        response = await mock_broker_execution_enabled.place_order(order)
        assert response.order_id == "MOCK-ORDER-001"

    async def test_get_holdings_always_works(self, mock_broker):
        """get_holdings is read-only and must never raise execution errors."""
        response = await mock_broker.get_holdings()
        assert len(response.holdings) > 0
        assert response.source == "mock_broker"

    async def test_holdings_contain_realistic_symbols(self, mock_broker):
        response = await mock_broker.get_holdings()
        symbols = [h.tradingsymbol for h in response.holdings]
        # Our mock includes these NSE symbols
        assert "RELIANCE" in symbols
        assert "BEL" in symbols
        assert "HAL" in symbols

    async def test_quote_returns_all_requested_symbols(self, mock_broker):
        symbols = ["RELIANCE", "BEL", "NONEXISTENT"]
        quotes = await mock_broker.get_quote(symbols)
        assert set(quotes.keys()) == set(symbols)

    def test_default_mock_has_execution_disabled(self):
        broker = MockBrokerGateway()
        assert broker._execution_enabled is False


class TestBrokerGatewayAbstractContract:
    def test_cannot_instantiate_abstract_gateway(self):
        from investing_agent.gateway.base import BrokerGateway
        with pytest.raises(TypeError):
            BrokerGateway()  # type: ignore[abstract]
