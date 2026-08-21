from __future__ import annotations

"""Outcome scoring for Phase 6C walk-forward decisions.

score_outcome is the only function in this feature that is allowed to see
prices with trading_date > decision_at -- it is only ever called after a
WalkForwardDecision already exists (frozen, persisted, immutable), so there
is no way for a future price to influence the decision itself.

Corporate-action events are loaded without an as_of restriction here
(unlike decisions.py's freeze path) because scoring an outcome is explicitly
the step where using everything now known, including facts recorded after
decision_at, is correct and intended -- a split discovered/recorded later
still needs to be applied to reconcile a raw pre/post-split price pair.

Every numeric field is None exactly when it cannot be genuinely computed;
data_quality_notes carries the specific per-horizon reason.
"""

import calendar
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import WalkForwardDecision, WalkForwardOutcome
from investing_agent.db.repositories.prices import DailyPriceRepository
from investing_agent.db.repositories.walkforward import WalkForwardOutcomeRepository
from investing_agent.schemas.walkforward import OutcomeStatus, WalkForwardOutcomeCreate
from investing_agent.services.reconstruction.corporate_actions import load_corporate_action_events
from investing_agent.services.reconstruction.fifo import CorporateActionEvent
from investing_agent.services.walkforward.prices import (
    resolve_benchmark_entry_price,
    resolve_benchmark_horizon_price,
    resolve_entry_price,
    resolve_horizon_price,
)
from investing_agent.services.walkforward.returns import (
    cumulative_split_factor,
    max_drawdown_pct,
    simple_return,
    split_adjusted_return,
)

BENCHMARK_CODE = "NIFTY_50"
_HORIZONS: list[tuple[int, str]] = [(1, "1m"), (3, "3m"), (6, "6m"), (12, "12m")]


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


async def score_outcome(
    session: AsyncSession, *, decision: WalkForwardDecision
) -> WalkForwardOutcome:
    symbol = decision.symbol
    decision_at = decision.decision_at

    events = [
        e for e in await load_corporate_action_events(session) if e.symbol == symbol
    ]

    entry = await resolve_entry_price(session, symbol, decision_at)
    benchmark_entry = await resolve_benchmark_entry_price(session, BENCHMARK_CODE, decision_at)

    notes: list[str] = []
    if entry.reason:
        notes.append(f"ENTRY: {entry.reason}")
    if benchmark_entry.reason:
        notes.append(f"ENTRY_BENCHMARK: {benchmark_entry.reason}")

    fields: dict[str, object] = {
        "entry_price": entry.price,
        "entry_price_date": entry.trading_date,
    }

    scored_count = 0
    widest_end_date: date | None = None

    for months, label in _HORIZONS:
        target_date = _add_months(decision_at, months)
        horizon = await resolve_horizon_price(session, symbol, target_date)
        benchmark_horizon = await resolve_benchmark_horizon_price(session, BENCHMARK_CODE, target_date)

        fields[f"price_{label}"] = horizon.price
        fields[f"price_{label}_date"] = horizon.trading_date
        if horizon.reason:
            notes.append(f"{label.upper()}: {horizon.reason}")
        if benchmark_horizon.reason:
            notes.append(f"{label.upper()}_BENCHMARK: {benchmark_horizon.reason}")

        stock_return: object = None
        benchmark_return: object = None
        excess_return: object = None

        if entry.price is not None and horizon.price is not None and horizon.trading_date is not None:
            factor = cumulative_split_factor(events, decision_at, horizon.trading_date)
            stock_return = split_adjusted_return(entry.price, horizon.price, factor)
            if stock_return is None:
                notes.append(f"{label.upper()}: entry price is zero, return undefined")

        if benchmark_entry.price is not None and benchmark_horizon.price is not None:
            benchmark_return = simple_return(benchmark_entry.price, benchmark_horizon.price)

        if stock_return is not None and benchmark_return is not None:
            excess_return = stock_return - benchmark_return

        fields[f"stock_return_{label}"] = stock_return
        fields[f"benchmark_return_{label}"] = benchmark_return
        fields[f"excess_return_{label}"] = excess_return

        if stock_return is not None:
            scored_count += 1
            if horizon.trading_date is not None:
                widest_end_date = (
                    horizon.trading_date
                    if widest_end_date is None or horizon.trading_date > widest_end_date
                    else widest_end_date
                )

    fields["max_drawdown_pct"] = await _compute_max_drawdown(
        session, symbol, decision_at, widest_end_date, events
    )

    outcome_status: OutcomeStatus
    if entry.price is None:
        outcome_status = "UNSCORABLE"
    elif scored_count == len(_HORIZONS):
        outcome_status = "SCORED"
    elif scored_count > 0:
        outcome_status = "PARTIAL"
    else:
        outcome_status = "UNSCORABLE"

    repo = WalkForwardOutcomeRepository(session)
    row, _created = await repo.create_if_not_exists(
        WalkForwardOutcomeCreate(
            decision_id=decision.id,
            outcome_status=outcome_status,
            data_quality_notes=notes,
            **fields,
        )
    )
    return row


async def _compute_max_drawdown(
    session: AsyncSession,
    symbol: str,
    decision_at: date,
    end_date: date | None,
    events: list[CorporateActionEvent],
):
    if end_date is None:
        return None
    repo = DailyPriceRepository(session)
    rows = await repo.list_between(symbol, decision_at, end_date)
    if len(rows) < 2:
        return None
    adjusted = []
    for row in rows:
        factor = cumulative_split_factor(events, row.trading_date, end_date)
        adjusted.append(row.close / factor)
    return max_drawdown_pct(adjusted)
