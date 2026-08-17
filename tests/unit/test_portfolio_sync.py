from __future__ import annotations

"""Unit tests for PortfolioSyncService pure logic.

These tests verify the stateless calculation logic inside PortfolioSyncService
without touching a real database.  The DB-interaction path (IngestionRun,
Holding persistence) is exercised in tests/integration/.

What is covered here:
  - _extract_holdings_list: all input shapes
  - _calculate_portfolio_weights: correct sum + individual weights
  - _normalize_one: ZERO_LAST_PRICE and MISSING_ISIN warnings
  - P&L maths: pnl == current_value - invested_value
  - Weight zero for empty portfolio
  - Error scenarios: auth/unavailable propagation (via mock session)
"""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from investing_agent.gateway.mock_reader import MockPortfolioReader
from investing_agent.gateway.portfolio_reader import BrokerAuthError, BrokerUnavailableError
from investing_agent.schemas.instruments import ResolvedInstrument
from investing_agent.schemas.sync import HoldingNormalized, ReconciliationWarning
from investing_agent.services.portfolio_sync import PortfolioSyncService

_ZERO = Decimal("0")


def _resolved(symbol: str = "X") -> ResolvedInstrument:
    return ResolvedInstrument(
        tradingsymbol=symbol,
        exchange="NSE",
        isin=None,
        instrument_type="EQ",
        company_id=uuid.uuid4(),
        company_symbol=symbol,
        resolution_method="created_new",
    )


def _make_service(scenario: str = "normal") -> PortfolioSyncService:
    """Build a PortfolioSyncService with all DB calls mocked out."""
    reader = MockPortfolioReader(scenario=scenario)
    session = AsyncMock()

    svc = PortfolioSyncService.__new__(PortfolioSyncService)
    svc._reader = reader
    svc._session = session
    svc._portfolio_repo = AsyncMock()
    svc._resolver = AsyncMock()
    svc._resolver.resolve = AsyncMock(side_effect=lambda h: _resolved(h.get("tradingsymbol", "X")))
    return svc


# ── _extract_holdings_list ─────────────────────────────────────────────────────

class TestExtractHoldingsList:
    def test_holdings_key(self) -> None:
        svc = _make_service()
        assert len(svc._extract_holdings_list({"holdings": [{"tradingsymbol": "X"}]})) == 1

    def test_data_key(self) -> None:
        svc = _make_service()
        assert len(svc._extract_holdings_list({"data": [{"tradingsymbol": "X"}]})) == 1

    def test_raw_list(self) -> None:
        svc = _make_service()
        assert len(svc._extract_holdings_list([{"tradingsymbol": "X"}])) == 1

    def test_empty_dict(self) -> None:
        svc = _make_service()
        assert svc._extract_holdings_list({}) == []

    def test_null_data(self) -> None:
        svc = _make_service()
        assert svc._extract_holdings_list({"data": None}) == []

    def test_null_holdings(self) -> None:
        svc = _make_service()
        assert svc._extract_holdings_list({"holdings": None}) == []

    def test_empty_holdings(self) -> None:
        svc = _make_service()
        assert svc._extract_holdings_list({"holdings": []}) == []


# ── _calculate_portfolio_weights ──────────────────────────────────────────────

class TestCalculatePortfolioWeights:
    def _holding(self, symbol: str, current_value: str) -> HoldingNormalized:
        v = Decimal(current_value)
        return HoldingNormalized(
            tradingsymbol=symbol, exchange="NSE", isin=None, instrument_type="EQ",
            company_id=uuid.uuid4(), quantity=1, t1_quantity=0,
            average_price=v, last_price=v, close_price=None,
            current_value=v, invested_value=v,
            pnl=_ZERO, pnl_pct=_ZERO, day_change=None, day_change_pct=None,
            portfolio_weight_pct=_ZERO, product="CNC", resolution_method="created_new",
        )

    def test_two_equal_holdings_each_50pct(self) -> None:
        svc = _make_service()
        holdings = [self._holding("A", "100"), self._holding("B", "100")]
        svc._calculate_portfolio_weights(holdings)
        assert holdings[0].portfolio_weight_pct == Decimal("50.0000")
        assert holdings[1].portfolio_weight_pct == Decimal("50.0000")

    def test_weights_sum_to_100(self) -> None:
        svc = _make_service()
        holdings = [
            self._holding("A", "26800"),
            self._holding("B", "32400"),
            self._holding("C", "43000"),
            self._holding("D", "17000"),
        ]
        svc._calculate_portfolio_weights(holdings)
        total = sum(h.portfolio_weight_pct for h in holdings)
        assert abs(total - Decimal("100")) < Decimal("0.01")

    def test_empty_portfolio_weights_zero(self) -> None:
        svc = _make_service()
        svc._calculate_portfolio_weights([])  # must not raise

    def test_single_holding_100pct(self) -> None:
        svc = _make_service()
        holdings = [self._holding("A", "50000")]
        svc._calculate_portfolio_weights(holdings)
        assert holdings[0].portfolio_weight_pct == Decimal("100.0000")


# ── _normalize_one ────────────────────────────────────────────────────────────

class TestNormalizeOne:
    @pytest.mark.asyncio
    async def test_normal_holding_no_warnings(self) -> None:
        svc = _make_service()
        raw = {
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "isin": "INE002A01018",
            "quantity": 10,
            "t1_quantity": 0,
            "average_price": 2450.0,
            "last_price": 2680.0,
            "instrument_type": "EQ",
        }
        norm, warns = await svc._normalize_one(raw)
        assert norm.tradingsymbol == "RELIANCE"
        assert norm.quantity == 10
        assert not warns

    @pytest.mark.asyncio
    async def test_zero_price_warning(self) -> None:
        svc = _make_service()
        raw = {
            "tradingsymbol": "SOMECO",
            "exchange": "NSE",
            "isin": "INE999X99999",
            "quantity": 10,
            "t1_quantity": 0,
            "average_price": 100.0,
            "last_price": 0.0,
            "instrument_type": "EQ",
        }
        _, warns = await svc._normalize_one(raw)
        codes = {w.code for w in warns}
        assert "ZERO_LAST_PRICE" in codes

    @pytest.mark.asyncio
    async def test_missing_isin_warning_for_eq(self) -> None:
        svc = _make_service()
        raw = {
            "tradingsymbol": "NOISIN",
            "exchange": "NSE",
            "isin": None,
            "quantity": 5,
            "t1_quantity": 0,
            "average_price": 100.0,
            "last_price": 110.0,
            "instrument_type": "EQ",
        }
        _, warns = await svc._normalize_one(raw)
        codes = {w.code for w in warns}
        assert "MISSING_ISIN" in codes

    @pytest.mark.asyncio
    async def test_etf_missing_isin_no_warning(self) -> None:
        svc = _make_service()
        raw = {
            "tradingsymbol": "GOLDBEES",
            "exchange": "NSE",
            "isin": None,
            "quantity": 50,
            "t1_quantity": 0,
            "average_price": 50.0,
            "last_price": 55.0,
            "instrument_type": "ETF",
        }
        _, warns = await svc._normalize_one(raw)
        codes = {w.code for w in warns}
        assert "MISSING_ISIN" not in codes

    @pytest.mark.asyncio
    async def test_pnl_calculation(self) -> None:
        svc = _make_service()
        raw = {
            "tradingsymbol": "TATA",
            "exchange": "NSE",
            "isin": "INE001A01036",
            "quantity": 10,
            "t1_quantity": 0,
            "average_price": 100.0,
            "last_price": 120.0,
            "instrument_type": "EQ",
        }
        norm, _ = await svc._normalize_one(raw)
        # pnl = (120 - 100) * 10 = 200
        assert norm.pnl == Decimal("200.0")
        assert norm.current_value == Decimal("1200.0")
        assert norm.invested_value == Decimal("1000.0")

    @pytest.mark.asyncio
    async def test_missing_tradingsymbol_raises(self) -> None:
        svc = _make_service()
        raw = {"exchange": "NSE", "quantity": 10}
        with pytest.raises(ValueError, match="tradingsymbol"):
            await svc._normalize_one(raw)


# ── Error propagation ─────────────────────────────────────────────────────────

class TestErrorPropagation:
    @pytest.mark.asyncio
    async def test_auth_error_propagates(self) -> None:
        reader = MockPortfolioReader(scenario="auth_expired")
        svc = PortfolioSyncService.__new__(PortfolioSyncService)
        svc._reader = reader
        svc._session = AsyncMock()
        svc._session.add = MagicMock()
        svc._session.flush = AsyncMock()

        ingestion_run_mock = MagicMock()
        ingestion_run_mock.id = uuid.uuid4()
        ingestion_run_mock.status = "running"

        with patch("investing_agent.services.portfolio_sync.IngestionRun", return_value=ingestion_run_mock):
            with pytest.raises(BrokerAuthError):
                await svc.sync("user1")

    @pytest.mark.asyncio
    async def test_unavailable_error_propagates(self) -> None:
        reader = MockPortfolioReader(scenario="unavailable")
        svc = PortfolioSyncService.__new__(PortfolioSyncService)
        svc._reader = reader
        svc._session = AsyncMock()
        svc._session.add = MagicMock()
        svc._session.flush = AsyncMock()

        ingestion_run_mock = MagicMock()
        ingestion_run_mock.id = uuid.uuid4()
        ingestion_run_mock.status = "running"

        with patch("investing_agent.services.portfolio_sync.IngestionRun", return_value=ingestion_run_mock):
            with pytest.raises(BrokerUnavailableError):
                await svc.sync("user1")
