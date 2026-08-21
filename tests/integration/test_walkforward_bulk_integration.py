from __future__ import annotations

"""Integration tests for Phase 6D bulk walk-forward evaluation against a
real PostgreSQL database.

Covers: decision-point generation from real trade history (including same-
day/same-symbol trade collapsing), end-to-end bulk orchestration, and the
audit-row exclusion/enrichment logic (RECONSTRUCTION_WARNING,
UNSCORABLE_NO_ENTRY_PRICE, WASH_TRADE_HOLD, holding-age, concentration).

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from investing_agent.schemas.broker_history import BrokerAccountCreate, HistoricalTradeCreate
from investing_agent.services.ingestion.common import ensure_company
from investing_agent.services.walkforward.aggregation import build_report
from investing_agent.services.walkforward.audit import build_audit_rows
from investing_agent.services.walkforward.bulk import generate_decision_points
from investing_agent.services.walkforward.outcomes import BENCHMARK_CODE
from investing_agent.services.walkforward.runner import run_bulk_walk_forward

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


class TestGenerateDecisionPoints:
    async def test_distinct_symbol_dates_collapse_same_day_trades(
        self, db_session, broker_account
    ) -> None:
        symbol = f"GEN{uuid.uuid4().hex[:4].upper()}"
        # Two trades, same symbol, same day -- must collapse to one point.
        await _seed_trade(db_session, broker_account, symbol, "BUY", 5, 100, date(2021, 1, 4), "T1")
        await _seed_trade(db_session, broker_account, symbol, "BUY", 5, 101, date(2021, 1, 4), "T2")
        await _seed_trade(db_session, broker_account, symbol, "SELL", 3, 110, date(2021, 3, 1), "T3")

        points = await generate_decision_points(db_session, broker_account.id)
        matching = [p for p in points if p[0] == symbol]
        assert matching == [(symbol, date(2021, 1, 4)), (symbol, date(2021, 3, 1))]


class TestRunBulkWalkForwardOrchestration:
    async def test_end_to_end_generates_and_scores_every_real_trade_date(
        self, db_session, broker_account
    ) -> None:
        symbol_a = f"BULKA{uuid.uuid4().hex[:4].upper()}"
        symbol_b = f"BULKB{uuid.uuid4().hex[:4].upper()}"

        await _seed_trade(db_session, broker_account, symbol_a, "BUY", 10, 100, date(2021, 1, 4), "A1")
        await _seed_trade(db_session, broker_account, symbol_a, "SELL", 10, 120, date(2021, 3, 1), "A2")
        await _seed_trade(db_session, broker_account, symbol_b, "BUY", 5, 200, date(2021, 2, 1), "B1")

        for d, close in [(date(2021, 1, 4), 100), (date(2021, 2, 4), 105), (date(2021, 3, 1), 120), (date(2021, 4, 1), 130)]:
            await _seed_daily_price(db_session, symbol_a, d, close)
        for d, close in [(date(2021, 2, 1), 200), (date(2021, 3, 1), 210)]:
            await _seed_daily_price(db_session, symbol_b, d, close)
        for d, close in [
            (date(2021, 1, 4), 15000), (date(2021, 2, 1), 15050), (date(2021, 2, 4), 15100),
            (date(2021, 3, 1), 15300), (date(2021, 4, 1), 15400),
        ]:
            await _seed_benchmark_price(db_session, d, close)

        result = await run_bulk_walk_forward(db_session, broker_account_id=broker_account.id)

        # 3 distinct (symbol, trade_date) points -> 2 decisions each (ACTUAL + HOLD_BASELINE)
        our_entries = [e for e in result.entries if e.decision.symbol in (symbol_a, symbol_b)]
        assert len(our_entries) == 6

        actual_actions = {
            (e.decision.symbol, e.decision.decision_at): e.decision.action
            for e in our_entries
            if e.decision.decision_source == "ACTUAL"
        }
        assert actual_actions[(symbol_a, date(2021, 1, 4))] == "BUY"
        assert actual_actions[(symbol_a, date(2021, 3, 1))] == "EXIT"
        assert actual_actions[(symbol_b, date(2021, 2, 1))] == "BUY"


class TestBuildAuditRows:
    async def test_clean_scorable_row_is_included(self, db_session, broker_account) -> None:
        symbol = f"CLN{uuid.uuid4().hex[:4].upper()}"
        decision_at = date(2021, 1, 4)
        await _seed_trade(db_session, broker_account, symbol, "BUY", 10, 100, decision_at, "T1")
        await _seed_daily_price(db_session, symbol, decision_at, 100)
        await _seed_daily_price(db_session, symbol, date(2021, 2, 4), 110)
        await _seed_benchmark_price(db_session, decision_at, 15000)
        await _seed_benchmark_price(db_session, date(2021, 2, 4), 15150)

        result = await run_bulk_walk_forward(db_session, broker_account_id=broker_account.id)
        rows = await build_audit_rows(
            db_session, broker_account_id=broker_account.id, entries=result.entries
        )
        row = next(r for r in rows if r.symbol == symbol)

        assert row.included_in_aggregate is True
        assert row.exclusion_reason is None
        assert row.action == "BUY"
        assert row.holding_age_days == 0
        assert row.stock_return["1m"] == Decimal("0.1")

    async def test_reconstruction_warning_row_is_excluded(self, db_session, broker_account) -> None:
        symbol = f"WARNX{uuid.uuid4().hex[:4].upper()}"
        # Oversell with no matching open lot -> reconstruction warning naming this symbol.
        await _seed_trade(db_session, broker_account, symbol, "SELL", 50, 100, date(2021, 1, 4), "T1")
        await _seed_daily_price(db_session, symbol, date(2021, 1, 4), 100)

        result = await run_bulk_walk_forward(db_session, broker_account_id=broker_account.id)
        rows = await build_audit_rows(
            db_session, broker_account_id=broker_account.id, entries=result.entries
        )
        row = next(r for r in rows if r.symbol == symbol)

        assert row.data_quality_status == "RECONSTRUCTION_WARNING"
        assert row.included_in_aggregate is False
        assert row.exclusion_reason == "RECONSTRUCTION_WARNING"

    async def test_no_entry_price_row_is_excluded_as_unscorable(
        self, db_session, broker_account
    ) -> None:
        symbol = f"NOPX{uuid.uuid4().hex[:4].upper()}"
        # A real trade exists, but no DailyPrice was ever recorded for this symbol.
        await _seed_trade(db_session, broker_account, symbol, "BUY", 10, 100, date(2021, 1, 4), "T1")

        result = await run_bulk_walk_forward(db_session, broker_account_id=broker_account.id)
        rows = await build_audit_rows(
            db_session, broker_account_id=broker_account.id, entries=result.entries
        )
        row = next(r for r in rows if r.symbol == symbol)

        assert row.outcome_status == "UNSCORABLE"
        assert row.included_in_aggregate is False
        assert row.exclusion_reason == "UNSCORABLE_NO_ENTRY_PRICE"

    async def test_wash_trade_hold_is_excluded(self, db_session, broker_account) -> None:
        symbol = f"WASH{uuid.uuid4().hex[:4].upper()}"
        decision_at = date(2021, 1, 4)
        # Same-day buy+sell of equal size nets to no quantity change -> HOLD.
        # Full price/benchmark coverage so this row is otherwise CLEAN and
        # SCORED, isolating WASH_TRADE_HOLD as the exclusion reason (rather
        # than it being masked by an unrelated UNSCORABLE outcome).
        await _seed_trade(db_session, broker_account, symbol, "BUY", 10, 100, decision_at, "T1")
        await _seed_trade(db_session, broker_account, symbol, "SELL", 10, 101, decision_at, "T2")
        await _seed_daily_price(db_session, symbol, decision_at, 100)
        await _seed_daily_price(db_session, symbol, date(2021, 2, 4), 105)
        await _seed_benchmark_price(db_session, decision_at, 15000)
        await _seed_benchmark_price(db_session, date(2021, 2, 4), 15100)

        result = await run_bulk_walk_forward(db_session, broker_account_id=broker_account.id)
        rows = await build_audit_rows(
            db_session, broker_account_id=broker_account.id, entries=result.entries
        )
        row = next(r for r in rows if r.symbol == symbol)

        assert row.action == "HOLD"
        assert row.included_in_aggregate is False
        assert row.exclusion_reason == "WASH_TRADE_HOLD"

    async def test_holding_age_and_concentration_are_computed(
        self, db_session, broker_account
    ) -> None:
        symbol_a = f"HOLDA{uuid.uuid4().hex[:4].upper()}"
        symbol_b = f"HOLDB{uuid.uuid4().hex[:4].upper()}"
        # symbol_a opened well before the ADD decision -> nonzero holding age.
        await _seed_trade(db_session, broker_account, symbol_a, "BUY", 10, 100, date(2021, 1, 4), "A1")
        await _seed_trade(db_session, broker_account, symbol_a, "BUY", 10, 100, date(2021, 4, 4), "A2")
        # symbol_b: a same-day, same-value position for a known concentration ratio.
        await _seed_trade(db_session, broker_account, symbol_b, "BUY", 10, 100, date(2021, 4, 4), "B1")
        for d, close in [(date(2021, 1, 4), 100), (date(2021, 4, 4), 100), (date(2021, 5, 4), 105)]:
            await _seed_daily_price(db_session, symbol_a, d, close)
        for d, close in [(date(2021, 4, 4), 100), (date(2021, 5, 4), 105)]:
            await _seed_daily_price(db_session, symbol_b, d, close)
        for d, close in [
            (date(2021, 1, 4), 15000), (date(2021, 4, 4), 15200), (date(2021, 5, 4), 15300),
        ]:
            await _seed_benchmark_price(db_session, d, close)

        result = await run_bulk_walk_forward(db_session, broker_account_id=broker_account.id)
        rows = await build_audit_rows(
            db_session, broker_account_id=broker_account.id, entries=result.entries
        )
        add_row = next(r for r in rows if r.symbol == symbol_a and r.decision_at == date(2021, 4, 4))
        new_row = next(r for r in rows if r.symbol == symbol_b and r.decision_at == date(2021, 4, 4))

        assert add_row.holding_age_days == (date(2021, 4, 4) - date(2021, 1, 4)).days
        assert new_row.holding_age_days == 0
        # symbol_a holds 20 shares @ cost 100 (two buys) = 2000 invested;
        # symbol_b holds 10 shares @ cost 100 = 1000 invested; total = 3000.
        assert add_row.invested_capital == Decimal("2000")
        assert new_row.invested_capital == Decimal("1000")
        assert add_row.concentration_pct == Decimal("2000") / Decimal("3000")
        assert new_row.concentration_pct == Decimal("1000") / Decimal("3000")


class TestReportFromRealAuditRows:
    async def test_build_report_reflects_real_scored_data(self, db_session, broker_account) -> None:
        symbol = f"RPT{uuid.uuid4().hex[:4].upper()}"
        decision_at = date(2021, 1, 4)
        await _seed_trade(db_session, broker_account, symbol, "BUY", 10, 100, decision_at, "T1")
        await _seed_daily_price(db_session, symbol, decision_at, 100)
        await _seed_daily_price(db_session, symbol, date(2021, 2, 4), 110)
        await _seed_benchmark_price(db_session, decision_at, 15000)
        await _seed_benchmark_price(db_session, date(2021, 2, 4), 15100)

        result = await run_bulk_walk_forward(db_session, broker_account_id=broker_account.id)
        rows = await build_audit_rows(
            db_session, broker_account_id=broker_account.id, entries=result.entries
        )
        our_rows = [r for r in rows if r.symbol == symbol]
        report = build_report(our_rows)

        assert report["n_events_total"] == 1
        assert report["n_events_included"] == 1
        h1 = report["by_horizon"]["1m"]
        assert h1["overall"]["n_scored_this_horizon"] == 1
        assert h1["by_action"]["BUY"]["median_stock_return_pct"] == Decimal("10")
