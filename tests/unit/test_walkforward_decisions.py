from __future__ import annotations

"""Unit tests for classify_actual_action's BUY/ADD/REDUCE/EXIT/HOLD truth
table (services/walkforward/decisions.py). freeze_decision itself needs a
real DB session (portfolio reconstruction), so it's covered by the
integration suite instead."""

from decimal import Decimal
from unittest.mock import MagicMock

from investing_agent.services.walkforward.decisions import classify_actual_action

_TRADE = MagicMock()


class TestClassifyActualAction:
    def test_buy_when_no_prior_position_and_now_holding(self):
        assert classify_actual_action(Decimal("0"), Decimal("100"), [_TRADE]) == "BUY"

    def test_add_when_position_grows(self):
        assert classify_actual_action(Decimal("100"), Decimal("150"), [_TRADE]) == "ADD"

    def test_reduce_when_position_shrinks_but_not_closed(self):
        assert classify_actual_action(Decimal("150"), Decimal("100"), [_TRADE]) == "REDUCE"

    def test_exit_when_position_fully_closed(self):
        assert classify_actual_action(Decimal("100"), Decimal("0"), [_TRADE]) == "EXIT"

    def test_hold_when_no_trades_that_date(self):
        assert classify_actual_action(Decimal("100"), Decimal("100"), []) == "HOLD"

    def test_hold_when_trade_happened_but_quantity_unchanged(self):
        # e.g. a same-day buy+sell of equal size — still HOLD by net effect.
        assert classify_actual_action(Decimal("100"), Decimal("100"), [_TRADE]) == "HOLD"

    def test_hold_with_zero_positions_and_no_trades(self):
        assert classify_actual_action(Decimal("0"), Decimal("0"), []) == "HOLD"


def test_decisions_module_never_imports_price_repositories():
    """Structural proof, not just a behavioral one: the module that builds
    a frozen decision must not even import DailyPriceRepository /
    BenchmarkPriceRepository, so future-price leakage into a decision is
    impossible regardless of what a caller does. Parses the actual import
    statements (via ast) rather than substring-matching the file, since the
    module's own docstring legitimately mentions these names in prose."""
    import ast

    import investing_agent.services.walkforward.decisions as decisions_module

    with open(decisions_module.__file__) as f:
        tree = ast.parse(f.read())

    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)

    assert "DailyPriceRepository" not in imported_names
    assert "BenchmarkPriceRepository" not in imported_names
    assert not any(name.startswith("investing_agent.services.walkforward.prices") for name in imported_names)
