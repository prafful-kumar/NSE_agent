from __future__ import annotations

"""Integration tests for Phase 6C walk-forward simulation against a real
PostgreSQL database.

Covers the 9 required scenarios: no future price leakage, no future
corporate-action leakage, weekend/holiday price lookup semantics,
insufficient future horizon, missing price / mid-series gap, corporate-
action crossing at the outcome level, benchmark alignment, unresolved-
position quality flag, and immutable frozen decision.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from investing_agent.schemas.broker_history import BrokerAccountCreate, HistoricalTradeCreate
from investing_agent.services.ingestion.common import ensure_company
from investing_agent.services.reconstruction.corporate_actions import record_corporate_action_event
from investing_agent.services.reconstruction.service import get_portfolio_as_of
from investing_agent.services.walkforward.decisions import freeze_decision
from investing_agent.services.walkforward.outcomes import BENCHMARK_CODE, score_outcome
from investing_agent.services.walkforward.runner import run_walk_forward

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session():
    import os

    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://investing:investing@localhost:5433/investing_agent_test"
    )
    from investing_agent.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def broker_account(db_session):
    from investing_agent.db.repositories.broker_history import BrokerAccountRepository

    repo = BrokerAccountRepository(db_session)
    account, _ = await repo.get_or_create(
        BrokerAccountCreate(
            user_id=f"user-{uuid.uuid4().hex[:8]}", broker="ZERODHA", account_label="primary"
        )
    )
    await db_session.flush()
    return account


@pytest.fixture
async def run(db_session, broker_account):
    from investing_agent.db.repositories.walkforward import WalkForwardRunRepository
    from investing_agent.schemas.walkforward import WalkForwardRunCreate

    repo = WalkForwardRunRepository(db_session)
    row = await repo.create(
        WalkForwardRunCreate(
            broker_account_id=broker_account.id,
            strategy_profile="LONG_TERM",
            horizons_months=[1, 3, 6, 12],
            model_version="walkforward-v1-test",
        )
    )
    await db_session.flush()
    return row


async def _seed_trade(db_session, broker_account, symbol, side, quantity, price, trade_date, key):
    from investing_agent.db.repositories.broker_history import HistoricalTradeRepository

    repo = HistoricalTradeRepository(db_session)
    await repo.create_if_not_exists(
        HistoricalTradeCreate(
            broker_account_id=broker_account.id,
            dedupe_key=key,
            symbol=symbol,
            trade_date=trade_date,
            side=side,
            quantity=Decimal(str(quantity)),
            price=Decimal(str(price)),
        )
    )
    await db_session.flush()


async def _seed_daily_price(db_session, symbol, trading_date, close, source="TEST"):
    from investing_agent.db.repositories.prices import DailyPriceRepository
    from investing_agent.schemas.prices import DailyPriceCreate

    company = await ensure_company(db_session, symbol)
    repo = DailyPriceRepository(db_session)
    close_d = Decimal(str(close))
    row, _ = await repo.create_if_not_exists(
        DailyPriceCreate(
            company_id=company.id, symbol=symbol, trading_date=trading_date,
            open=close_d, high=close_d, low=close_d, close=close_d, source=source,
        )
    )
    await db_session.flush()
    return row


async def _seed_benchmark_price(db_session, trading_date, close, benchmark_code=BENCHMARK_CODE):
    from investing_agent.db.repositories.prices import BenchmarkPriceRepository
    from investing_agent.schemas.prices import BenchmarkPriceCreate

    repo = BenchmarkPriceRepository(db_session)
    close_d = Decimal(str(close))
    row, _ = await repo.create_if_not_exists(
        BenchmarkPriceCreate(
            benchmark_code=benchmark_code, trading_date=trading_date,
            open=close_d, high=close_d, low=close_d, close=close_d, source="TEST",
        )
    )
    await db_session.flush()
    return row


class TestNoFuturePriceLeakage:
    async def test_freeze_decision_never_constructs_a_price_repository(
        self, db_session, broker_account, run
    ) -> None:
        await _seed_trade(
            db_session, broker_account, "LEAKTEST", "BUY", 10, 100, date(2021, 1, 4), "T1"
        )

        with (
            patch("investing_agent.db.repositories.prices.DailyPriceRepository") as daily_cls,
            patch("investing_agent.db.repositories.prices.BenchmarkPriceRepository") as bench_cls,
        ):
            daily_cls.side_effect = AssertionError("DailyPriceRepository must not be constructed")
            bench_cls.side_effect = AssertionError("BenchmarkPriceRepository must not be constructed")

            decision = await freeze_decision(
                db_session, run=run, broker_account_id=broker_account.id,
                symbol="LEAKTEST", decision_at=date(2021, 6, 15), decision_source="ACTUAL",
            )

        assert decision.quantity_held == Decimal("10")
        daily_cls.assert_not_called()
        bench_cls.assert_not_called()


class TestNoFutureCorporateActionLeakage:
    async def test_a_correction_recorded_after_decision_at_does_not_leak_in(
        self, db_session, broker_account, run
    ) -> None:
        symbol = f"CAEAK{uuid.uuid4().hex[:4].upper()}"
        await _seed_trade(db_session, broker_account, symbol, "BUY", 10, 100, date(2021, 1, 4), "T1")

        # Recorded "now" (test run time), i.e. long after decision_at below —
        # record_corporate_action_event has no as_of override, so available_at
        # defaults to insertion time.
        await record_corporate_action_event(
            db_session, symbol=symbol, event_type="SPLIT", event_date=date(2021, 3, 1),
            ratio_new=Decimal("10"), ratio_old=Decimal("1"), source="TEST",
        )
        await db_session.flush()

        decision = await freeze_decision(
            db_session, run=run, broker_account_id=broker_account.id,
            symbol=symbol, decision_at=date(2021, 6, 15), decision_source="ACTUAL",
        )
        # Frozen at T=2021-06-15: the split correction wasn't available yet
        # (available_at ~= today, 2026), so quantity must stay unadjusted.
        assert decision.quantity_held == Decimal("10")

        # Sanity check: the *current* view (no feature_cutoff_at) does see it.
        current_snapshot = await get_portfolio_as_of(
            db_session, broker_account_id=broker_account.id, as_of_date=date(2021, 6, 15)
        )
        current_position = next(p for p in current_snapshot.positions if p.symbol == symbol)
        assert current_position.quantity_held == Decimal("100")


class TestWeekendHolidayPriceLookup:
    async def test_nearest_before_and_after_a_saturday(self, db_session) -> None:
        from investing_agent.services.walkforward.prices import (
            resolve_entry_price,
            resolve_horizon_price,
        )

        symbol = f"WKND{uuid.uuid4().hex[:4].upper()}"
        friday = date(2021, 1, 1)  # Friday
        saturday = date(2021, 1, 2)
        monday = date(2021, 1, 4)
        await _seed_daily_price(db_session, symbol, friday, 100)
        await _seed_daily_price(db_session, symbol, monday, 110)

        entry = await resolve_entry_price(db_session, symbol, saturday)
        assert entry.price == Decimal("100")
        assert entry.trading_date == friday

        horizon = await resolve_horizon_price(db_session, symbol, saturday)
        assert horizon.price == Decimal("110")
        assert horizon.trading_date == monday


class TestInsufficientFutureHorizon:
    async def test_target_beyond_all_available_data_is_insufficient_horizon(
        self, db_session
    ) -> None:
        from investing_agent.services.walkforward.prices import resolve_horizon_price

        symbol = f"HORZ{uuid.uuid4().hex[:4].upper()}"
        await _seed_daily_price(db_session, symbol, date(2021, 1, 4), 100)
        # No data anywhere near or after this target date.
        result = await resolve_horizon_price(db_session, symbol, date(2021, 6, 1))

        assert result.price is None
        assert result.reason is not None
        assert "INSUFFICIENT_HORIZON" in result.reason


class TestMissingPriceDataGap:
    async def test_mid_series_gap_beyond_lookahead_window_is_data_gap(self, db_session) -> None:
        from investing_agent.services.walkforward.prices import resolve_horizon_price

        symbol = f"GAP{uuid.uuid4().hex[:4].upper()}"
        target = date(2021, 6, 1)
        # Data resumes well outside the default 10-day lookahead window, but
        # data does exist further out -- this must read as a gap, not
        # "insufficient horizon".
        await _seed_daily_price(db_session, symbol, date(2021, 7, 1), 100)

        result = await resolve_horizon_price(db_session, symbol, target)

        assert result.price is None
        assert result.reason is not None
        assert "DATA_GAP" in result.reason


class TestCorporateActionCrossingAtOutcomeLevel:
    async def test_raw_price_break_across_a_split_is_not_scored_as_a_loss(
        self, db_session, broker_account, run
    ) -> None:
        symbol = f"SPLIT{uuid.uuid4().hex[:4].upper()}"
        decision_at = date(2021, 1, 4)
        await _seed_trade(db_session, broker_account, symbol, "BUY", 10, 3000, decision_at, "T1")
        await _seed_daily_price(db_session, symbol, decision_at, 3000)
        # Split lands between decision_at and the 1M horizon.
        await record_corporate_action_event(
            db_session, symbol=symbol, event_type="SPLIT", event_date=date(2021, 1, 15),
            ratio_new=Decimal("10"), ratio_old=Decimal("1"), source="TEST",
        )
        horizon_date = date(2021, 2, 4)
        await _seed_daily_price(db_session, symbol, horizon_date, 320)
        await _seed_benchmark_price(db_session, decision_at, 15000)
        await _seed_benchmark_price(db_session, horizon_date, 15100)

        decision = await freeze_decision(
            db_session, run=run, broker_account_id=broker_account.id,
            symbol=symbol, decision_at=decision_at, decision_source="ACTUAL",
        )
        outcome = await score_outcome(db_session, decision=decision)

        assert outcome.stock_return_1m is not None
        # adjusted_start = 3000/10 = 300; return = 320/300 - 1 ~= 6.67%
        assert abs(outcome.stock_return_1m - Decimal("0.0666666666666666666666666667")) < Decimal("0.0001")
        assert outcome.stock_return_1m > Decimal("-0.5")  # nowhere near a fabricated -89% loss


class TestBenchmarkAlignment:
    async def test_excess_return_equals_stock_minus_benchmark(
        self, db_session, broker_account, run
    ) -> None:
        symbol = f"BENCH{uuid.uuid4().hex[:4].upper()}"
        decision_at = date(2021, 1, 4)
        horizon_date = date(2021, 2, 4)
        await _seed_trade(db_session, broker_account, symbol, "BUY", 10, 100, decision_at, "T1")
        await _seed_daily_price(db_session, symbol, decision_at, 100)
        await _seed_daily_price(db_session, symbol, horizon_date, 110)
        await _seed_benchmark_price(db_session, decision_at, 15000)
        await _seed_benchmark_price(db_session, horizon_date, 15300)

        decision = await freeze_decision(
            db_session, run=run, broker_account_id=broker_account.id,
            symbol=symbol, decision_at=decision_at, decision_source="ACTUAL",
        )
        outcome = await score_outcome(db_session, decision=decision)

        assert outcome.stock_return_1m == Decimal("0.1")
        assert outcome.benchmark_return_1m == Decimal("0.02")
        assert outcome.excess_return_1m == outcome.stock_return_1m - outcome.benchmark_return_1m
        assert outcome.excess_return_1m == Decimal("0.08")


class TestUnresolvedPositionQualityFlag:
    async def test_warning_on_one_symbol_does_not_taint_another(
        self, db_session, broker_account, run
    ) -> None:
        clean_symbol = f"CLEAN{uuid.uuid4().hex[:4].upper()}"
        warned_symbol = f"WARN{uuid.uuid4().hex[:4].upper()}"
        decision_at = date(2021, 6, 1)

        await _seed_trade(db_session, broker_account, clean_symbol, "BUY", 10, 100, date(2021, 1, 4), "T1")
        # An oversell (SELL exceeding any open lot) triggers a reconstruction
        # warning that mentions warned_symbol by name.
        await _seed_trade(db_session, broker_account, warned_symbol, "SELL", 50, 100, date(2021, 1, 4), "T2")

        clean_decision = await freeze_decision(
            db_session, run=run, broker_account_id=broker_account.id,
            symbol=clean_symbol, decision_at=decision_at, decision_source="ACTUAL",
        )
        warned_decision = await freeze_decision(
            db_session, run=run, broker_account_id=broker_account.id,
            symbol=warned_symbol, decision_at=decision_at, decision_source="ACTUAL",
        )

        assert clean_decision.data_quality_status == "CLEAN"
        assert clean_decision.reconstruction_warnings == []
        assert warned_decision.data_quality_status == "RECONSTRUCTION_WARNING"
        assert any(warned_symbol in w for w in warned_decision.reconstruction_warnings)


class TestImmutableFrozenDecision:
    async def test_refreezing_the_same_decision_is_a_no_op(
        self, db_session, broker_account, run
    ) -> None:
        from investing_agent.db.repositories.walkforward import WalkForwardDecisionRepository

        symbol = f"IMMUT{uuid.uuid4().hex[:4].upper()}"
        await _seed_trade(db_session, broker_account, symbol, "BUY", 10, 100, date(2021, 1, 4), "T1")

        first = await freeze_decision(
            db_session, run=run, broker_account_id=broker_account.id,
            symbol=symbol, decision_at=date(2021, 6, 15), decision_source="ACTUAL",
        )
        second = await freeze_decision(
            db_session, run=run, broker_account_id=broker_account.id,
            symbol=symbol, decision_at=date(2021, 6, 15), decision_source="ACTUAL",
        )

        assert first.id == second.id
        all_for_run = await WalkForwardDecisionRepository(db_session).list_for_run(run.id)
        matching = [d for d in all_for_run if d.symbol == symbol]
        assert len(matching) == 1

        # No update method is ever offered — the only mutation path is
        # create_if_not_exists, which is itself idempotent on the natural key.
        assert not hasattr(WalkForwardDecisionRepository, "update")


class TestRunWalkForwardOrchestration:
    async def test_end_to_end_produces_actual_and_hold_baseline_with_outcomes(
        self, db_session, broker_account
    ) -> None:
        symbol = f"E2E{uuid.uuid4().hex[:4].upper()}"
        decision_at = date(2021, 1, 4)
        horizon_date = date(2021, 2, 4)
        await _seed_trade(db_session, broker_account, symbol, "BUY", 10, 100, decision_at, "T1")
        await _seed_daily_price(db_session, symbol, decision_at, 100)
        await _seed_daily_price(db_session, symbol, horizon_date, 105)
        await _seed_benchmark_price(db_session, decision_at, 15000)
        await _seed_benchmark_price(db_session, horizon_date, 15150)

        result = await run_walk_forward(
            db_session, broker_account_id=broker_account.id,
            symbol_dates=[(symbol, decision_at)],
        )

        assert len(result.entries) == 2
        sources = {e.decision.decision_source for e in result.entries}
        assert sources == {"ACTUAL", "HOLD_BASELINE"}
        hold_entry = next(e for e in result.entries if e.decision.decision_source == "HOLD_BASELINE")
        assert hold_entry.decision.action == "HOLD"
        actual_entry = next(e for e in result.entries if e.decision.decision_source == "ACTUAL")
        assert actual_entry.decision.action == "BUY"
        for entry in result.entries:
            assert entry.outcome.entry_price == Decimal("100")
