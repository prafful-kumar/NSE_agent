from __future__ import annotations

"""Unit tests for replay.py: event ordering + cash-balance breakdown."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from investing_agent.services.reconstruction.replay import (
    compute_cash_breakdown,
    trades_to_events,
)


def _trade(symbol, side, quantity, price, trade_date, dedupe_key, trade_datetime=None):
    return SimpleNamespace(
        dedupe_key=dedupe_key,
        symbol=symbol,
        isin=None,
        company_id=None,
        trade_date=trade_date,
        trade_datetime=trade_datetime,
        side=side,
        quantity=Decimal(str(quantity)),
        price=Decimal(str(price)),
    )


def _cash_flow(flow_date, amount, dedupe_key=None):
    return SimpleNamespace(
        dedupe_key=dedupe_key or uuid.uuid4().hex,
        flow_date=flow_date,
        flow_type="DEPOSIT",
        amount=Decimal(str(amount)),
    )


class TestTradesToEvents:
    def test_sorted_by_date_then_datetime_then_dedupe_key(self) -> None:
        trades = [
            _trade("A", "BUY", 1, 100, date(2024, 1, 2), "T2"),
            _trade("A", "BUY", 1, 100, date(2024, 1, 1), "T1", trade_datetime=datetime(2024, 1, 1, 10, 0)),
            _trade("A", "BUY", 1, 100, date(2024, 1, 1), "T0", trade_datetime=datetime(2024, 1, 1, 9, 0)),
        ]
        events = trades_to_events(trades)
        assert [e.dedupe_key for e in events] == ["T0", "T1", "T2"]

    def test_missing_datetime_does_not_crash_sort(self) -> None:
        trades = [
            _trade("A", "BUY", 1, 100, date(2024, 1, 1), "T1", trade_datetime=None),
            _trade("A", "BUY", 1, 100, date(2024, 1, 1), "T2", trade_datetime=datetime(2024, 1, 1, 9, 0)),
        ]
        events = trades_to_events(trades)
        assert len(events) == 2  # no TypeError comparing date vs datetime


class TestCashBreakdown:
    def test_buy_debits_sell_credits_trade_only_cash(self) -> None:
        trades = [
            _trade("A", "BUY", 10, 100, date(2024, 1, 1), "T1"),
            _trade("A", "SELL", 4, 150, date(2024, 1, 5), "T2"),
        ]
        cash = compute_cash_breakdown(trades, [], ledger_coverage_start=None)
        assert cash.cash_from_trades_only == Decimal("-400")  # -1000 + 600
        assert cash.cash_ledger_delta == Decimal("0")
        assert cash.cash_balance_partial == Decimal("-400")

    def test_no_ledger_caveat_when_no_coverage_start(self) -> None:
        cash = compute_cash_breakdown([], [], ledger_coverage_start=None)
        assert "No cash-flow ledger has been imported" in cash.caveat

    def test_ledger_coverage_caveat_names_the_gap(self) -> None:
        cash_flows = [_cash_flow(date(2025, 8, 20), 1000)]
        cash = compute_cash_breakdown([], cash_flows, ledger_coverage_start=date(2025, 8, 20))
        assert cash.cash_ledger_delta == Decimal("1000")
        assert "2025-08-20" in cash.caveat
        assert cash.cash_ledger_coverage_start == date(2025, 8, 20)
