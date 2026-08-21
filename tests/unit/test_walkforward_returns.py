from __future__ import annotations

"""Unit tests for services/walkforward/returns.py's pure return math.

The ANGELONE-style 1:10 split regression is the load-bearing test here: a
raw 10x price break across a known corporate action must resolve to a small,
plausible return -- never a fabricated ~-90% loss (see returns.py's
docstring and split_adjusted_return).
"""

from datetime import date
from decimal import Decimal

from investing_agent.services.reconstruction.fifo import CorporateActionEvent
from investing_agent.services.walkforward.returns import (
    cumulative_split_factor,
    max_drawdown_pct,
    parse_ratio,
    simple_return,
    split_adjusted_return,
)


def _event(ratio_old: str, ratio_new: str, event_date: date, symbol: str = "ANGELONE") -> CorporateActionEvent:
    return CorporateActionEvent(
        symbol=symbol,
        event_type="SPLIT",
        event_date=event_date,
        ratio_new=Decimal(ratio_new),
        ratio_old=Decimal(ratio_old),
        source="TEST",
    )


class TestParseRatio:
    def test_parses_old_colon_new(self):
        assert parse_ratio("1:10") == (Decimal("1"), Decimal("10"))

    def test_none_is_none(self):
        assert parse_ratio(None) is None

    def test_unparseable_is_none_not_a_guess(self):
        assert parse_ratio("bonus") is None
        assert parse_ratio("one:ten") is None


class TestCumulativeSplitFactor:
    def test_no_events_in_window_is_identity(self):
        events = [_event("1", "10", date(2026, 6, 1))]
        factor = cumulative_split_factor(events, date(2026, 1, 1), date(2026, 3, 1))
        assert factor == Decimal("1")

    def test_event_within_window_is_included(self):
        events = [_event("1", "10", date(2026, 2, 26))]
        factor = cumulative_split_factor(events, date(2026, 1, 1), date(2026, 3, 1))
        assert factor == Decimal("10")

    def test_event_exactly_on_start_date_is_excluded(self):
        events = [_event("1", "10", date(2026, 1, 1))]
        factor = cumulative_split_factor(events, date(2026, 1, 1), date(2026, 3, 1))
        assert factor == Decimal("1")

    def test_event_exactly_on_end_date_is_included(self):
        events = [_event("1", "10", date(2026, 3, 1))]
        factor = cumulative_split_factor(events, date(2026, 1, 1), date(2026, 3, 1))
        assert factor == Decimal("10")

    def test_multiple_events_compound(self):
        events = [_event("1", "2", date(2026, 2, 1)), _event("1", "5", date(2026, 2, 15))]
        factor = cumulative_split_factor(events, date(2026, 1, 1), date(2026, 3, 1))
        assert factor == Decimal("10")


class TestSplitAdjustedReturn:
    def test_angelone_1_to_10_split_does_not_register_as_a_90pct_loss(self):
        # Real ANGELONE fixture: raw pre-split price ~3000, raw post-split
        # price ~320 -- a naive (end-start)/start would read as -89.3%.
        events = [_event("1", "10", date(2026, 2, 26))]
        factor = cumulative_split_factor(events, date(2026, 1, 1), date(2026, 3, 1))
        result = split_adjusted_return(Decimal("3000"), Decimal("320"), factor)
        assert result is not None
        # adjusted_start = 3000/10 = 300; return = 320/300 - 1 = 6.67%
        assert abs(result - Decimal("0.0666666666666666666666666667")) < Decimal("0.0001")
        assert result > Decimal("-0.5")  # sanity: nowhere near a fabricated loss

    def test_no_crossing_event_is_a_plain_return(self):
        result = split_adjusted_return(Decimal("100"), Decimal("110"), Decimal("1"))
        assert result == Decimal("0.1")

    def test_zero_start_price_is_none(self):
        assert split_adjusted_return(Decimal("0"), Decimal("100"), Decimal("1")) is None


class TestSimpleReturn:
    def test_basic(self):
        assert simple_return(Decimal("100"), Decimal("120")) == Decimal("0.2")

    def test_zero_start_is_none(self):
        assert simple_return(Decimal("0"), Decimal("100")) is None


class TestMaxDrawdownPct:
    def test_fewer_than_two_points_is_none(self):
        assert max_drawdown_pct([Decimal("100")]) is None
        assert max_drawdown_pct([]) is None

    def test_monotonic_rise_has_zero_drawdown(self):
        result = max_drawdown_pct([Decimal("100"), Decimal("110"), Decimal("120")])
        assert result == Decimal("0")

    def test_peak_then_trough_computes_negative_drawdown(self):
        result = max_drawdown_pct([Decimal("100"), Decimal("200"), Decimal("150")])
        assert result == Decimal("-0.25")
