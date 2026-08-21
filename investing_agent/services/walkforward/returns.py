from __future__ import annotations

"""Pure, DB-agnostic return math for Phase 6C walk-forward outcomes.

No I/O anywhere in this module -- mirrors services/backtesting/scoring.py's
purity. Every function takes already-resolved inputs and returns a value
(or None when the inputs don't support a real answer); callers in
services/walkforward/outcomes.py are responsible for loading prices and
corporate-action events.

DailyPrice is raw/unadjusted (Phase 6C.0), so a stock's start/end price pair
that straddles a split or bonus needs the same ratio math Phase 6B's FIFO
engine already applies to lots (fifo.py::_apply_corporate_action): quantity
scales by ratio_new/ratio_old, cost/price scales by the inverse. For a
return spanning such an event, the earlier (start) price is adjusted onto
the later price's share-count basis by dividing by the cumulative split
factor -- this is what keeps a raw 10x/5x/2x price break from ever being
misread as an actual investment loss or gain.
"""

from decimal import Decimal

from investing_agent.services.reconstruction.corporate_actions import parse_ratio
from investing_agent.services.reconstruction.fifo import CorporateActionEvent

__all__ = [
    "parse_ratio",
    "cumulative_split_factor",
    "split_adjusted_return",
    "simple_return",
    "max_drawdown_pct",
]


def cumulative_split_factor(
    events: list[CorporateActionEvent], start_date, end_date
) -> Decimal:
    """Product of ratio_new/ratio_old for every SPLIT/BONUS event with
    start_date < event_date <= end_date (i.e. events strictly after the
    entry point and on or before the evaluation point -- an event dated
    exactly on start_date is treated as already reflected in the start
    price, consistent with how decision_at itself is excluded from the
    corporate-action lookback in outcomes.py).

    Returns Decimal("1") when no event falls in the window -- a true
    no-op, not a guess."""
    factor = Decimal("1")
    for event in events:
        if start_date < event.event_date <= end_date:
            factor *= event.ratio_new / event.ratio_old
    return factor


def split_adjusted_return(
    start_price: Decimal, end_price: Decimal, factor: Decimal
) -> Decimal | None:
    """Return over [start_price, end_price] where start_price is expressed
    in pre-split share terms and end_price in post-split share terms,
    reconciled via the cumulative split factor between them. factor=1 means
    no crossing event, so this reduces to a simple_return. Returns None if
    start_price is zero (a genuinely undefined return, not a fabricated
    one)."""
    if start_price == 0:
        return None
    adjusted_start_price = start_price / factor
    if adjusted_start_price == 0:
        return None
    return (end_price / adjusted_start_price) - Decimal("1")


def simple_return(start_value: Decimal, end_value: Decimal) -> Decimal | None:
    """Plain (end - start) / start, for values with no split concept (e.g.
    a benchmark index). None if start_value is zero."""
    if start_value == 0:
        return None
    return (end_value - start_value) / start_value


def max_drawdown_pct(adjusted_closes: list[Decimal]) -> Decimal | None:
    """Largest peak-to-trough decline (as a negative fraction, e.g.
    Decimal("-0.15") for -15%) over an already split-adjusted close series,
    in chronological order. None if there are fewer than 2 points --
    approximate/best-effort by design (Phase 6C.0's known EQ/BE-series
    reclassification gaps mean this series can have holes); never blocks
    the rest of the outcome row."""
    if len(adjusted_closes) < 2:
        return None
    running_max = adjusted_closes[0]
    worst = Decimal("0")
    for price in adjusted_closes[1:]:
        running_max = max(running_max, price)
        if running_max == 0:
            continue
        drawdown = (price - running_max) / running_max
        worst = min(worst, drawdown)
    return worst
