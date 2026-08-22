from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from investing_agent.cli import cli
from investing_agent.services import valuation

AS_OF = datetime(2026, 8, 21, tzinfo=UTC)
COMPANY = SimpleNamespace(id="company", symbol="BEL")


class _Result:
    def __init__(self, value): self.value = value
    def scalar_one_or_none(self): return self.value


def _earnings() -> valuation.EarningsSeries:
    return valuation.EarningsSeries("TTM", Decimal("8"), Decimal("8"), Decimal("8"), ("r1", "r2", "r3", "r4"), (), "document")


@pytest.mark.asyncio
async def test_missing_financial_is_insufficient_evidence(monkeypatch):
    monkeypatch.setattr(valuation, "_latest_financial", AsyncMock(return_value=None))
    outcome = await valuation.generate_for_company(AsyncMock(), company=COMPANY, as_of=AS_OF)
    assert outcome.gaps == ("verified_financial_missing_or_stale",)


@pytest.mark.asyncio
async def test_missing_ttm_never_annualises_a_quarter(monkeypatch):
    monkeypatch.setattr(valuation, "_latest_financial", AsyncMock(return_value=SimpleNamespace(statement_scope="standalone")))
    monkeypatch.setattr(valuation, "_ttm_earnings", AsyncMock(return_value=None))
    outcome = await valuation.generate_for_company(AsyncMock(), company=COMPANY, as_of=AS_OF)
    assert outcome.gaps == ("pit_ttm_eps_missing_or_noncontiguous",)


def test_real_bel_and_hal_next_quarter_eps_are_incompatible_with_ttm_pe():
    bel_q1_actual, bel_q2_estimate = Decimal("1.43"), Decimal("1.60")
    hal_q1_actual, hal_q2_estimate = Decimal("23.63"), Decimal("20.89")
    assert bel_q1_actual + bel_q2_estimate != bel_q2_estimate * 4
    assert hal_q1_actual + hal_q2_estimate != hal_q2_estimate * 4
    assert not valuation.horizons_compatible(earnings_horizon="NEXT_QUARTER", multiple_horizon="TTM")
    assert valuation.horizons_compatible(earnings_horizon="TTM", multiple_horizon="TTM")


@pytest.mark.asyncio
async def test_incompatible_multiple_is_rejected(monkeypatch):
    monkeypatch.setattr(valuation, "_latest_financial", AsyncMock(return_value=SimpleNamespace(statement_scope="standalone")))
    monkeypatch.setattr(valuation, "_ttm_earnings", AsyncMock(return_value=_earnings()))
    multiple = SimpleNamespace(earnings_horizon="NEXT_QUARTER", pe_low=Decimal("10"), pe_mid=Decimal("12"), pe_high=Decimal("14"))
    session = AsyncMock(); session.execute.return_value = _Result(multiple)
    outcome = await valuation.generate_for_company(session, company=COMPANY, as_of=AS_OF)
    assert outcome.gaps == ("incompatible_eps_and_pe_horizons",)


@pytest.mark.asyncio
async def test_missing_price_is_rejected(monkeypatch):
    monkeypatch.setattr(valuation, "_latest_financial", AsyncMock(return_value=SimpleNamespace(statement_scope="standalone")))
    monkeypatch.setattr(valuation, "_ttm_earnings", AsyncMock(return_value=_earnings()))
    multiple = SimpleNamespace(id="multiple", earnings_horizon="TTM", pe_low=Decimal("10"), pe_mid=Decimal("12"), pe_high=Decimal("14"))
    session = AsyncMock(); session.execute.side_effect = [_Result(multiple), _Result(None)]
    outcome = await valuation.generate_for_company(session, company=COMPANY, as_of=AS_OF)
    assert outcome.gaps == ("raw_market_price_missing",)


@pytest.mark.asyncio
async def test_repeat_is_immutable(monkeypatch):
    monkeypatch.setattr(valuation, "_latest_financial", AsyncMock(return_value=SimpleNamespace(statement_scope="standalone")))
    monkeypatch.setattr(valuation, "_ttm_earnings", AsyncMock(return_value=_earnings()))
    multiple = SimpleNamespace(id="multiple", earnings_horizon="TTM", pe_low=Decimal("10"), pe_mid=Decimal("12"), pe_high=Decimal("14"))
    price = SimpleNamespace(id="price", close=Decimal("50"), trading_date=AS_OF.date())
    existing = SimpleNamespace(id="existing")
    session = AsyncMock(); session.execute.side_effect = [_Result(multiple), _Result(price), _Result(existing)]
    outcome = await valuation.generate_for_company(session, company=COMPANY, as_of=AS_OF)
    assert outcome.snapshot is existing
    session.add.assert_not_called()


def test_explicit_symbol_valuation_cli_does_not_use_watchlist_context(monkeypatch):
    runner = AsyncMock(); monkeypatch.setattr("investing_agent.cli._valuation_run", runner)
    result = CliRunner().invoke(cli, ["valuation-run", "BEL", "HAL", "--as-of", "2026-08-21"])
    assert result.exit_code == 0
    runner.assert_awaited_once()


def test_explicit_symbol_recommendation_cli_keeps_current_rule_runner(monkeypatch):
    runner = AsyncMock(); monkeypatch.setattr("investing_agent.cli._recommendation_run", runner)
    result = CliRunner().invoke(cli, ["recommendation-run", "BEL", "HAL", "--as-of", "2026-08-21"])
    assert result.exit_code == 0
    runner.assert_awaited_once()
