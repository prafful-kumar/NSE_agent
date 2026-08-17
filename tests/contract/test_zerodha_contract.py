from __future__ import annotations

"""Contract tests for Zerodha PortfolioReader interface.

These tests use MockPortfolioReader (backed by JSON fixtures) to verify that
every scenario the reader can return is handled correctly by the normalisation
layer.  They act as a contract: if Zerodha changes its response shape, these
tests should catch the regression before any real money is at risk.

No database or real broker required — all assertions are on in-memory objects.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from investing_agent.gateway.mock_reader import MockPortfolioReader
from investing_agent.gateway.portfolio_reader import (
    BrokerAuthError,
    BrokerTimeoutError,
    BrokerUnavailableError,
)
from investing_agent.services.portfolio_sync import PortfolioSyncService

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "zerodha"

# ── Helper ────────────────────────────────────────────────────────────────────


def _extract_holdings(raw: dict) -> list[dict]:
    """Mirror PortfolioSyncService._extract_holdings_list logic."""
    if isinstance(raw, list):
        return raw
    if "data" in raw:
        return raw["data"] or []
    if "holdings" in raw:
        return raw["holdings"] or []
    return []


# ── Fixture file contract tests ───────────────────────────────────────────────


class TestHoldingsNormalFixture:
    """Validate the structure of the normal holdings fixture."""

    def test_fixture_loads(self) -> None:
        with open(FIXTURE_DIR / "holdings_normal.json") as f:
            data = json.load(f)
        assert "holdings" in data

    def test_fixture_has_four_holdings(self) -> None:
        with open(FIXTURE_DIR / "holdings_normal.json") as f:
            data = json.load(f)
        assert len(data["holdings"]) == 4

    def test_each_holding_has_required_fields(self) -> None:
        required = {"tradingsymbol", "exchange", "quantity", "average_price", "last_price"}
        with open(FIXTURE_DIR / "holdings_normal.json") as f:
            data = json.load(f)
        for h in data["holdings"]:
            missing = required - h.keys()
            assert not missing, f"{h.get('tradingsymbol')} missing: {missing}"

    def test_all_have_isin(self) -> None:
        with open(FIXTURE_DIR / "holdings_normal.json") as f:
            data = json.load(f)
        for h in data["holdings"]:
            assert h.get("isin"), f"{h['tradingsymbol']} missing ISIN"

    def test_isin_format(self) -> None:
        with open(FIXTURE_DIR / "holdings_normal.json") as f:
            data = json.load(f)
        for h in data["holdings"]:
            isin = h.get("isin", "")
            assert len(isin) == 12, f"Bad ISIN length for {h['tradingsymbol']}: {isin}"
            assert isin[:2] == "IN", f"Indian ISIN must start with IN: {isin}"

    def test_prices_positive(self) -> None:
        with open(FIXTURE_DIR / "holdings_normal.json") as f:
            data = json.load(f)
        for h in data["holdings"]:
            assert h["average_price"] > 0
            assert h["last_price"] > 0
            assert h["quantity"] > 0

    def test_instrument_type_present(self) -> None:
        with open(FIXTURE_DIR / "holdings_normal.json") as f:
            data = json.load(f)
        for h in data["holdings"]:
            assert h.get("instrument_type") in ("EQ", "ETF", "MF", "BE", "BL", "GB", "GS")


class TestHoldingsEmptyFixture:
    def test_empty_holdings(self) -> None:
        with open(FIXTURE_DIR / "holdings_empty.json") as f:
            data = json.load(f)
        assert data["holdings"] == []


class TestHoldingsWithEtfFixture:
    def test_has_etf_entry(self) -> None:
        with open(FIXTURE_DIR / "holdings_with_etf.json") as f:
            data = json.load(f)
        types = {h["instrument_type"] for h in data["holdings"]}
        assert "ETF" in types

    def test_etf_has_isin(self) -> None:
        with open(FIXTURE_DIR / "holdings_with_etf.json") as f:
            data = json.load(f)
        for h in data["holdings"]:
            if h["instrument_type"] == "ETF":
                assert h.get("isin"), "ETF must have ISIN"


class TestHoldingsMissingPriceFixture:
    def test_zero_last_price(self) -> None:
        with open(FIXTURE_DIR / "holdings_missing_price.json") as f:
            data = json.load(f)
        assert data["holdings"][0]["last_price"] == 0.0

    def test_quantity_still_present(self) -> None:
        with open(FIXTURE_DIR / "holdings_missing_price.json") as f:
            data = json.load(f)
        assert data["holdings"][0]["quantity"] > 0


class TestPositionsNormalFixture:
    def test_has_net_key(self) -> None:
        with open(FIXTURE_DIR / "positions_normal.json") as f:
            data = json.load(f)
        assert "net" in data

    def test_net_positions_have_pnl(self) -> None:
        with open(FIXTURE_DIR / "positions_normal.json") as f:
            data = json.load(f)
        for pos in data["net"]:
            assert "pnl" in pos


# ── MockPortfolioReader contract tests ─────────────────────────────────────────


class TestMockReaderScenarios:
    """Verify MockPortfolioReader matches the corresponding fixture files."""

    @pytest.mark.asyncio
    async def test_normal_returns_four_holdings(self) -> None:
        reader = MockPortfolioReader(scenario="normal")
        result = await reader.get_holdings()
        holdings = _extract_holdings(result)
        assert len(holdings) == 4

    @pytest.mark.asyncio
    async def test_empty_returns_zero_holdings(self) -> None:
        reader = MockPortfolioReader(scenario="empty")
        result = await reader.get_holdings()
        holdings = _extract_holdings(result)
        assert len(holdings) == 0

    @pytest.mark.asyncio
    async def test_with_etf_has_etf(self) -> None:
        reader = MockPortfolioReader(scenario="with_etf")
        result = await reader.get_holdings()
        holdings = _extract_holdings(result)
        types = {h["instrument_type"] for h in holdings}
        assert "ETF" in types

    @pytest.mark.asyncio
    async def test_missing_price_has_zero(self) -> None:
        reader = MockPortfolioReader(scenario="missing_price")
        result = await reader.get_holdings()
        holdings = _extract_holdings(result)
        assert any(h["last_price"] == 0.0 for h in holdings)

    @pytest.mark.asyncio
    async def test_auth_expired_raises_broker_auth_error(self) -> None:
        reader = MockPortfolioReader(scenario="auth_expired")
        with pytest.raises(BrokerAuthError):
            await reader.get_holdings()

    @pytest.mark.asyncio
    async def test_unavailable_raises_broker_unavailable_error(self) -> None:
        reader = MockPortfolioReader(scenario="unavailable")
        with pytest.raises(BrokerUnavailableError):
            await reader.get_holdings()

    @pytest.mark.asyncio
    async def test_timeout_raises_broker_timeout_error(self) -> None:
        reader = MockPortfolioReader(scenario="timeout")
        with pytest.raises(BrokerTimeoutError):
            await reader.get_holdings()

    @pytest.mark.asyncio
    async def test_ping_returns_true_for_normal(self) -> None:
        reader = MockPortfolioReader(scenario="normal")
        assert await reader.ping() is True

    @pytest.mark.asyncio
    async def test_ping_returns_false_for_unavailable(self) -> None:
        reader = MockPortfolioReader(scenario="unavailable")
        assert await reader.ping() is False

    @pytest.mark.asyncio
    async def test_fixture_file_loading(self) -> None:
        reader = MockPortfolioReader(fixture_file="holdings_normal.json")
        result = await reader.get_holdings()
        holdings = _extract_holdings(result)
        assert len(holdings) == 4

    @pytest.mark.asyncio
    async def test_get_positions_normal(self) -> None:
        reader = MockPortfolioReader(scenario="normal")
        result = await reader.get_positions()
        assert "net" in result

    @pytest.mark.asyncio
    async def test_get_margins_normal(self) -> None:
        reader = MockPortfolioReader(scenario="normal")
        result = await reader.get_margins()
        assert "equity" in result

    @pytest.mark.asyncio
    async def test_get_quotes_returns_dict(self) -> None:
        reader = MockPortfolioReader(scenario="normal")
        result = await reader.get_quotes(["NSE:RELIANCE", "NSE:HDFCBANK"])
        assert "NSE:RELIANCE" in result
        assert "last_price" in result["NSE:RELIANCE"]


# ── Extract holdings list contract ─────────────────────────────────────────────


class TestExtractHoldingsList:
    """Verify _extract_holdings_list handles all known Zerodha response shapes."""

    def test_holdings_key(self) -> None:
        svc = _make_sync_service()
        data = {"holdings": [{"tradingsymbol": "X"}]}
        assert len(svc._extract_holdings_list(data)) == 1

    def test_data_key(self) -> None:
        svc = _make_sync_service()
        data = {"data": [{"tradingsymbol": "X"}]}
        assert len(svc._extract_holdings_list(data)) == 1

    def test_raw_list(self) -> None:
        svc = _make_sync_service()
        data = [{"tradingsymbol": "X"}]
        assert len(svc._extract_holdings_list(data)) == 1

    def test_empty_dict_returns_empty(self) -> None:
        svc = _make_sync_service()
        assert svc._extract_holdings_list({}) == []

    def test_null_data_returns_empty(self) -> None:
        svc = _make_sync_service()
        assert svc._extract_holdings_list({"data": None}) == []


def _make_sync_service() -> PortfolioSyncService:
    """Return a PortfolioSyncService with no DB (only call pure methods)."""
    from unittest.mock import MagicMock
    reader = MockPortfolioReader(scenario="normal")
    session = MagicMock()
    svc = PortfolioSyncService.__new__(PortfolioSyncService)
    svc._reader = reader
    svc._session = session
    return svc
