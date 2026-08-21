from __future__ import annotations

"""Event-sourced replay (Phase 6B): converts fetched HistoricalTrade /
HistoricalCashFlow rows into an ordered FIFO event stream and a
cash-balance breakdown.

Chronological ordering only matters *within a single symbol* (for correct
FIFO lot matching) -- the cumulative cash figures are simple totals with no
ordering dependency at all. So unlike a full event-sourced ledger, this
deliberately does NOT interleave trades and cash flows into one merged
timeline; that would be over-engineering for what "portfolio as of date X"
actually needs: FIFO lot state, plus two independent running totals.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from investing_agent.db.models import HistoricalCashFlow, HistoricalTrade
from investing_agent.services.reconstruction.fifo import (
    CorporateActionEvent,
    FifoReplayResult,
    OpeningPositionAdjustment,
    TradeEvent,
    replay_fifo,
)

CASH_LEDGER_COVERAGE_CAVEAT = (
    "cash_balance_partial excludes deposits/withdrawals/charges before the "
    "earliest imported ledger row ({coverage_start}); only trade-implied cash "
    "movement (buy/sell debit/credit) is known for the full account history."
)
NO_LEDGER_CAVEAT = (
    "No cash-flow ledger has been imported for this account -- "
    "cash_balance_partial reflects trade-implied cash movement only."
)


@dataclass
class CashBreakdown:
    cash_from_trades_only: Decimal
    cash_ledger_delta: Decimal
    cash_ledger_coverage_start: date | None
    caveat: str

    @property
    def cash_balance_partial(self) -> Decimal:
        return self.cash_from_trades_only + self.cash_ledger_delta


def trades_to_events(trades: list[HistoricalTrade]) -> list[TradeEvent]:
    events = [
        TradeEvent(
            dedupe_key=t.dedupe_key,
            symbol=t.symbol,
            isin=t.isin,
            company_id=t.company_id,
            trade_date=t.trade_date,
            trade_datetime=t.trade_datetime,
            side=t.side,
            quantity=t.quantity,
            price=t.price,
        )
        for t in trades
    ]
    # Sort within each symbol by (trade_date, trade_datetime, dedupe_key) for
    # deterministic, correct FIFO matching. A global sort by this same key is
    # sufficient since replay_fifo processes each symbol independently.
    return sorted(
        events,
        key=lambda e: (
            e.trade_date,
            e.trade_datetime or datetime.combine(e.trade_date, time.min),
            e.dedupe_key,
        ),
    )


def replay_trades(
    trades: list[HistoricalTrade],
    *,
    corporate_actions: list[CorporateActionEvent] | None = None,
    opening_adjustments: list[OpeningPositionAdjustment] | None = None,
) -> FifoReplayResult:
    return replay_fifo(
        trades_to_events(trades),
        corporate_actions=corporate_actions,
        opening_adjustments=opening_adjustments,
    )


def compute_cash_breakdown(
    trades: list[HistoricalTrade],
    cash_flows: list[HistoricalCashFlow],
    ledger_coverage_start: date | None,
) -> CashBreakdown:
    cash_from_trades = Decimal("0")
    for t in trades:
        signed_value = t.quantity * t.price
        cash_from_trades += -signed_value if t.side == "BUY" else signed_value

    cash_ledger_delta = sum((cf.amount for cf in cash_flows), Decimal("0"))

    caveat = (
        CASH_LEDGER_COVERAGE_CAVEAT.format(coverage_start=ledger_coverage_start)
        if ledger_coverage_start is not None
        else NO_LEDGER_CAVEAT
    )

    return CashBreakdown(
        cash_from_trades_only=cash_from_trades,
        cash_ledger_delta=cash_ledger_delta,
        cash_ledger_coverage_start=ledger_coverage_start,
        caveat=caveat,
    )
