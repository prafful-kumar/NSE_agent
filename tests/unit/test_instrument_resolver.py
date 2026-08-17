from __future__ import annotations

"""Unit tests for InstrumentResolver resolution logic.

Uses AsyncMock to fake the repository layer so no DB is needed.
Tests cover all 5 resolution paths:
  1. ISIN hit in instrument_master
  2. symbol+exchange hit in instrument_master
  3. ISIN hit in companies table
  4. symbol hit in companies table
  5. Create new company stub (no prior knowledge)
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from investing_agent.schemas.instruments import ResolvedInstrument
from investing_agent.services.instrument_resolver import InstrumentResolver


def _company(symbol: str, isin: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        symbol=symbol,
        isin=isin,
        name=symbol,
        exchange="NSE",
    )


def _instrument(symbol: str, exchange: str = "NSE", isin: str | None = None,
                company_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        tradingsymbol=symbol,
        exchange=exchange,
        isin=isin,
        instrument_type="EQ",
        company_id=company_id,
    )


def _make_resolver(
    isin_hit: InstrumentMaster | None = None,
    sym_hit: InstrumentMaster | None = None,
    company_isin_hit: Company | None = None,
    company_sym_hit: Company | None = None,
) -> InstrumentResolver:
    """Build an InstrumentResolver with fully mocked repositories."""
    resolver = InstrumentResolver.__new__(InstrumentResolver)

    instr_repo = AsyncMock()
    instr_repo.get_by_isin = AsyncMock(return_value=isin_hit)
    instr_repo.get_by_symbol_exchange = AsyncMock(return_value=sym_hit)
    instr_repo.upsert = AsyncMock(return_value=isin_hit or sym_hit or MagicMock())

    company_repo = AsyncMock()
    company_repo.get_by_isin = AsyncMock(return_value=company_isin_hit)
    company_repo.get_by_symbol = AsyncMock(return_value=company_sym_hit)
    stub = _company("UNKNOWN99")
    company_repo.upsert = AsyncMock(return_value=stub)

    resolver._instrument_repo = instr_repo
    resolver._company_repo = company_repo
    resolver._session = AsyncMock()
    return resolver


# ── Resolution paths ──────────────────────────────────────────────────────────

_RELIANCE_RAW = {
    "tradingsymbol": "RELIANCE",
    "exchange": "NSE",
    "isin": "INE002A01018",
    "instrument_type": "EQ",
}

_NO_ISIN_RAW = {
    "tradingsymbol": "UNKNOWN99",
    "exchange": "NSE",
    "isin": None,
    "instrument_type": "EQ",
}


@pytest.mark.asyncio
async def test_path1_isin_hit_in_instrument_master() -> None:
    company_id = uuid.uuid4()
    instr = _instrument("RELIANCE", isin="INE002A01018", company_id=company_id)
    resolver = _make_resolver(isin_hit=instr)

    result = await resolver.resolve(_RELIANCE_RAW)

    assert result.company_id == company_id
    assert result.resolution_method == "isin"


@pytest.mark.asyncio
async def test_path2_symbol_exchange_hit_in_instrument_master() -> None:
    company_id = uuid.uuid4()
    instr = _instrument("RELIANCE", isin="INE002A01018", company_id=company_id)
    resolver = _make_resolver(isin_hit=None, sym_hit=instr)

    result = await resolver.resolve(_RELIANCE_RAW)

    assert result.company_id == company_id
    assert result.resolution_method == "symbol_exchange"


@pytest.mark.asyncio
async def test_path3_isin_hit_in_companies() -> None:
    company_id = uuid.uuid4()
    company = _company("RELIANCE", isin="INE002A01018")
    company.id = company_id
    resolver = _make_resolver(isin_hit=None, sym_hit=None, company_isin_hit=company)

    result = await resolver.resolve(_RELIANCE_RAW)

    assert result.company_id == company_id
    assert result.resolution_method == "isin"


@pytest.mark.asyncio
async def test_path4_symbol_hit_in_companies() -> None:
    company_id = uuid.uuid4()
    company = _company("RELIANCE")
    company.id = company_id
    resolver = _make_resolver(isin_hit=None, sym_hit=None, company_isin_hit=None,
                              company_sym_hit=company)

    result = await resolver.resolve(_RELIANCE_RAW)

    assert result.company_id == company_id
    assert result.resolution_method == "symbol_exchange"


@pytest.mark.asyncio
async def test_path5_create_new_stub() -> None:
    resolver = _make_resolver()  # all lookups return None → stub created

    result = await resolver.resolve(_NO_ISIN_RAW)

    assert result.resolution_method == "created_new"
    assert result.company_id is not None


@pytest.mark.asyncio
async def test_resolution_result_is_correct_schema() -> None:
    resolver = _make_resolver()
    result = await resolver.resolve(_NO_ISIN_RAW)
    assert isinstance(result, ResolvedInstrument)


@pytest.mark.asyncio
async def test_resolution_with_isin_populates_isin() -> None:
    company_id = uuid.uuid4()
    instr = _instrument("RELIANCE", isin="INE002A01018", company_id=company_id)
    resolver = _make_resolver(isin_hit=instr)

    result = await resolver.resolve(_RELIANCE_RAW)
    assert result.isin == "INE002A01018"


@pytest.mark.asyncio
async def test_path2_preserves_isin_from_instrument_if_not_in_holding() -> None:
    company_id = uuid.uuid4()
    # Holding has no ISIN; instrument_master knows it
    instr = _instrument("RELIANCE", isin="INE002A01018", company_id=company_id)
    resolver = _make_resolver(isin_hit=None, sym_hit=instr)

    raw_no_isin = {**_RELIANCE_RAW, "isin": None}
    result = await resolver.resolve(raw_no_isin)

    assert result.isin == "INE002A01018"


@pytest.mark.asyncio
async def test_all_resolution_methods_are_valid_values() -> None:
    allowed = {"isin", "symbol_exchange", "created_new", "unresolved"}

    _ISIN = "INE000X00001"  # 12-char valid-length ISIN for testing
    for scenario, kwargs in [
        ("path1", {"isin_hit": _instrument("X", isin=_ISIN, company_id=uuid.uuid4())}),
        ("path2", {"sym_hit": _instrument("X", company_id=uuid.uuid4())}),
        ("path3", {"company_isin_hit": _company("X", isin=_ISIN)}),
        ("path4", {"company_sym_hit": _company("X")}),
        ("path5", {}),
    ]:
        resolver = _make_resolver(**kwargs)
        raw = {"tradingsymbol": "X", "exchange": "NSE", "isin": _ISIN, "instrument_type": "EQ"}
        result = await resolver.resolve(raw)
        assert result.resolution_method in allowed, (
            f"Scenario {scenario}: unexpected method {result.resolution_method!r}"
        )
