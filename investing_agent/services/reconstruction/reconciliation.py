from __future__ import annotations

"""Reconciles the FIFO reconstruction against Zerodha's own P&L/holdings
exports (Phase 6B) -- a first-class check that reconstructed numbers stay
consistent with Zerodha's own summary numbers across the known no-trade
gap windows, not just an afterthought.

Symbols that appear in a P&L export with nonzero Open Quantity/Unrealized
P&L but zero rows in historical_trades (e.g. ABFRL-RE-R, a rights-
entitlement artifact with no corresponding BUY/SELL row) are an expected,
already-diagnosed gap -- reported in expected_gaps_corporate_action rather
than divergent_symbols, so the report doesn't cry wolf on a known
limitation (see fifo.py's oversell/synthetic-lot handling for the trade-
side counterpart of this same limitation).
"""

import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.repositories.broker_history import HistoricalTradeRepository
from investing_agent.schemas.reconstruction import (
    ReconciliationQualityScore,
    ReconciliationReport,
    ReconciliationRowDiff,
)
from investing_agent.services.reconstruction.corporate_actions import (
    load_corporate_action_events,
    load_opening_adjustments,
)
from investing_agent.services.reconstruction.fifo import (
    CorporateActionEvent,
    OpeningPositionAdjustment,
    replay_fifo,
)
from investing_agent.services.reconstruction.pnl_report_parser import (
    HoldingsStatement,
    PnlStatement,
    parse_holdings_statement,
    parse_pnl_statement,
)
from investing_agent.services.reconstruction.replay import trades_to_events
from investing_agent.services.reconstruction.service import get_portfolio_as_of

_TOLERANCE = Decimal("1")  # INR; statement figures are rounded, ours are exact


def _period_realized_pnl_by_symbol(
    trades,
    period_start,
    period_end,
    *,
    corporate_actions: list[CorporateActionEvent],
    opening_adjustments: list[OpeningPositionAdjustment],
) -> dict[str, Decimal]:
    """FIFO realized P&L attributable to SELL trades within
    [period_start, period_end], matched against the FULL trade history up
    to period_end (so lots opened before period_start are still correctly
    matched) -- only the resulting realized-P&L *deltas* from sells inside
    the window are attributed to the period."""
    in_window_symbols = {t.symbol for t in trades if period_start <= t.trade_date <= period_end}

    # replay_fifo aggregates realized P&L per symbol across the whole replay,
    # not per sell event -- so the period-scoped figure is computed as a
    # diff of two full replays: everything up to period_end, minus
    # everything strictly before period_start (lots opened earlier are still
    # correctly matched in both replays; only the resulting delta is
    # attributed to the period).
    result = replay_fifo(
        trades_to_events([t for t in trades if t.trade_date <= period_end]),
        corporate_actions=corporate_actions,
        opening_adjustments=opening_adjustments,
    )
    baseline = replay_fifo(
        trades_to_events([t for t in trades if t.trade_date < period_start]),
        corporate_actions=corporate_actions,
        opening_adjustments=opening_adjustments,
    )

    period_realized: dict[str, Decimal] = {}
    for symbol, total in result.realized_pnl_by_symbol.items():
        base = baseline.realized_pnl_by_symbol.get(symbol, Decimal("0"))
        delta = total - base
        if delta != 0 or symbol in in_window_symbols:
            period_realized[symbol] = delta

    return period_realized


async def reconcile_pnl_statement(
    session: AsyncSession,
    *,
    broker_account_id: uuid.UUID,
    pnl: PnlStatement,
    known_corporate_action_symbols: set[str],
    corporate_actions: list[CorporateActionEvent],
    opening_adjustments: list[OpeningPositionAdjustment],
) -> tuple[list[ReconciliationRowDiff], list[str], int]:
    trade_repo = HistoricalTradeRepository(session)
    trades = await trade_repo.list_up_to(broker_account_id, pnl.period_end)

    period_realized = _period_realized_pnl_by_symbol(
        trades,
        pnl.period_start,
        pnl.period_end,
        corporate_actions=corporate_actions,
        opening_adjustments=opening_adjustments,
    )

    diffs: list[ReconciliationRowDiff] = []
    expected_gaps: list[str] = []
    checked = 0

    for row in pnl.rows:
        no_trades_at_all = not any(t.symbol == row.symbol for t in trades)
        if no_trades_at_all and (row.open_quantity != 0 or row.unrealized_pnl != 0):
            expected_gaps.append(row.symbol)
            known_corporate_action_symbols.add(row.symbol)
            continue

        checked += 1
        reconstructed_realized = period_realized.get(row.symbol, Decimal("0"))
        delta = reconstructed_realized - row.realized_pnl
        if abs(delta) > _TOLERANCE:
            diffs.append(
                ReconciliationRowDiff(
                    statement_file=pnl.file_name,
                    symbol=row.symbol,
                    field="realized_pnl",
                    expected=row.realized_pnl,
                    actual=reconstructed_realized,
                    delta=delta,
                    likely_cause="possible untracked corporate action or charges/rounding",
                )
            )

    return diffs, expected_gaps, checked


async def reconcile_holdings_statement(
    session: AsyncSession,
    *,
    broker_account_id: uuid.UUID,
    holdings: HoldingsStatement,
    known_corporate_action_symbols: set[str],
) -> tuple[list[ReconciliationRowDiff], int]:
    snapshot = await get_portfolio_as_of(
        session, broker_account_id=broker_account_id, as_of_date=holdings.as_of_date
    )
    reconstructed_by_symbol = {p.symbol: p for p in snapshot.positions}

    diffs: list[ReconciliationRowDiff] = []
    checked = 0
    for row in holdings.rows:
        if row.symbol in known_corporate_action_symbols:
            continue
        checked += 1
        position = reconstructed_by_symbol.get(row.symbol)
        if position is None:
            diffs.append(
                ReconciliationRowDiff(
                    statement_file=holdings.file_name,
                    symbol=row.symbol,
                    field="quantity_held",
                    expected=row.quantity_available,
                    actual=Decimal("0"),
                    delta=-row.quantity_available,
                    likely_cause="no open lots reconstructed for this symbol",
                )
            )
            continue

        qty_delta = position.quantity_held - row.quantity_available
        if abs(qty_delta) > _TOLERANCE:
            diffs.append(
                ReconciliationRowDiff(
                    statement_file=holdings.file_name,
                    symbol=row.symbol,
                    field="quantity_held",
                    expected=row.quantity_available,
                    actual=position.quantity_held,
                    delta=qty_delta,
                    likely_cause="possible untracked corporate action",
                )
            )

        cost_delta = position.average_cost - row.average_price
        if abs(cost_delta) > _TOLERANCE:
            diffs.append(
                ReconciliationRowDiff(
                    statement_file=holdings.file_name,
                    symbol=row.symbol,
                    field="average_cost",
                    expected=row.average_price,
                    actual=position.average_cost,
                    delta=cost_delta,
                    likely_cause="charges/rounding or untracked corporate action",
                )
            )

    return diffs, checked


async def reconcile_account(
    session: AsyncSession,
    *,
    broker_account_id: uuid.UUID,
    statement_files: list[Path],
) -> ReconciliationReport:
    pnl_files = [f for f in statement_files if f.name.startswith("pnl-")]
    holdings_files = [f for f in statement_files if f.name.startswith("holdings-")]

    corporate_actions = await load_corporate_action_events(session)
    opening_adjustments = await load_opening_adjustments(session, broker_account_id)

    all_diffs: list[ReconciliationRowDiff] = []
    expected_gaps: set[str] = set()
    warnings: list[str] = []
    symbols_checked: set[str] = set()
    pnl_rows_checked = 0
    holdings_rows_checked = 0

    for pnl_path in pnl_files:
        try:
            pnl = parse_pnl_statement(pnl_path)
        except ValueError as exc:
            warnings.append(str(exc))
            continue
        diffs, gaps, checked = await reconcile_pnl_statement(
            session,
            broker_account_id=broker_account_id,
            pnl=pnl,
            known_corporate_action_symbols=expected_gaps,
            corporate_actions=corporate_actions,
            opening_adjustments=opening_adjustments,
        )
        all_diffs.extend(diffs)
        pnl_rows_checked += checked
        symbols_checked.update(row.symbol for row in pnl.rows)

    for holdings_path in holdings_files:
        try:
            holdings = parse_holdings_statement(holdings_path)
        except ValueError as exc:
            warnings.append(str(exc))
            continue
        diffs, checked = await reconcile_holdings_statement(
            session,
            broker_account_id=broker_account_id,
            holdings=holdings,
            known_corporate_action_symbols=expected_gaps,
        )
        all_diffs.extend(diffs)
        holdings_rows_checked += checked
        symbols_checked.update(row.symbol for row in holdings.rows)

    divergent_symbols = sorted({d.symbol for d in all_diffs} - expected_gaps)
    # Drop diffs for symbols now known to be corporate-action gaps (a later
    # holdings file might flag a symbol before an earlier P&L file's row was
    # bucketed) -- keep the report's two buckets mutually exclusive.
    all_diffs = [d for d in all_diffs if d.symbol not in expected_gaps]

    overall_status = "CLEAN" if not divergent_symbols else "DIVERGENT"

    qty_mismatches = sum(1 for d in all_diffs if d.field == "quantity_held")
    cost_mismatches = sum(1 for d in all_diffs if d.field == "average_cost")
    realized_mismatches = sum(1 for d in all_diffs if d.field == "realized_pnl")

    quality_score = ReconciliationQualityScore(
        holdings_quantity_match_pct=_match_pct(holdings_rows_checked, qty_mismatches),
        holdings_avg_cost_match_pct=_match_pct(holdings_rows_checked, cost_mismatches),
        realized_pnl_match_pct=_match_pct(pnl_rows_checked, realized_mismatches),
        unresolved_symbol_count=len(divergent_symbols),
    )

    return ReconciliationReport(
        broker_account_id=broker_account_id,
        overall_status=overall_status,
        total_symbols_checked=len(symbols_checked),
        divergent_symbols=divergent_symbols,
        expected_gaps_corporate_action=sorted(expected_gaps),
        row_diffs=all_diffs,
        warnings=warnings,
        quality_score=quality_score,
    )


def _match_pct(checked: int, mismatches: int) -> float | None:
    if checked == 0:
        return None
    return round((checked - mismatches) / checked * 100, 2)
