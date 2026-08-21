from __future__ import annotations

"""Event-level audit table for Phase 6D bulk walk-forward evaluation.

build_audit_rows pairs each (symbol, decision_at)'s ACTUAL and HOLD_BASELINE
WalkForwardEntry (already frozen/scored by the unmodified Phase 6C pipeline)
into one AuditRow, and adds two purely descriptive, DB-derived fields that
were not worth threading into the immutable WalkForwardDecision schema
itself: holding_age_days (first trade of this symbol at or before
decision_at -- always PIT-safe by construction) and concentration_pct
(this position's invested capital / total invested capital across the whole
portfolio at decision_at, via the same get_portfolio_as_of already used
elsewhere -- no market prices involved, so no leakage risk).

Every AuditRow traces back to its source: decision_id/hold_decision_id ->
WalkForwardDecision -> trade_evidence -> HistoricalTrade.id. Nothing here
changes freeze_decision's or score_outcome's rules; this module only reads
their already-persisted output and decides, per the fixed criteria below,
whether a row counts toward the aggregate report.

score_outcome computes stock_return purely from (symbol, decision_at) -- it
never looks at quantity_held, so an ACTUAL and a HOLD_BASELINE decision for
the same (symbol, decision_at) always score identical price-return series.
"Actual vs HOLD" can therefore not be measured by diffing stock_return
between the two sources (that diff is always exactly zero); it has to be
measured at the position level, via how many shares the ACTUAL decision
actually added or removed relative to the HOLD_BASELINE quantity
(hold_quantity_held, captured below) -- see aggregation.py's
decision_dollar_impact.

Exclusion is a strict superset check, most-specific-reason-first:
- RECONSTRUCTION_WARNING: the ACTUAL decision's reconstruction touched this
  symbol with an unresolved warning (oversell/synthetic lot from an
  untracked corporate action, or a reconciliation-derived opening estimate)
  -- see decisions.py::_symbol_data_quality.
- UNSCORABLE_NO_ENTRY_PRICE: no entry price could be resolved at all (no
  EQ-series price on or before decision_at) -- nothing to score.
- WASH_TRADE_HOLD: the day's trades net to no quantity change, so
  classify_actual_action returned HOLD -- not one of BUY/ADD/REDUCE/SELL.
A row can still be UNSCORABLE-per-horizon (insufficient future horizon /
mid-series data gap) while remaining included: those individual horizon
returns are simply None and are excluded from that horizon's own stats by
aggregation.py, without invalidating the other horizons.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.repositories.broker_history import HistoricalTradeRepository
from investing_agent.services.reconstruction.service import get_portfolio_as_of
from investing_agent.services.walkforward.runner import WalkForwardEntry

_HORIZONS = ("1m", "3m", "6m", "12m")


@dataclass(frozen=True)
class AuditRow:
    symbol: str
    decision_at: date
    decision_id: uuid.UUID
    hold_decision_id: uuid.UUID | None
    action: str
    data_quality_status: str
    reconstruction_warnings: list[str]
    outcome_status: str
    quantity_held: Decimal
    invested_capital: Decimal
    hold_quantity_held: Decimal | None
    holding_age_days: int | None
    concentration_pct: Decimal | None
    calendar_year: int
    entry_price: Decimal | None
    stock_return: dict[str, Decimal | None]
    benchmark_return: dict[str, Decimal | None]
    excess_return: dict[str, Decimal | None]
    hold_stock_return: dict[str, Decimal | None]
    max_drawdown_pct: Decimal | None
    data_quality_notes: list[str]
    included_in_aggregate: bool
    exclusion_reason: str | None
    trade_evidence: list[dict[str, Any]] = field(default_factory=list)
    benchmark_tri_return: dict[str, Decimal | None] = field(default_factory=dict)
    excess_return_tri: dict[str, Decimal | None] = field(default_factory=dict)


def _horizon_dict(outcome, prefix: str) -> dict[str, Decimal | None]:
    if outcome is None:
        return dict.fromkeys(_HORIZONS)
    return {h: getattr(outcome, f"{prefix}_{h}") for h in _HORIZONS}


def _exclusion_reason(data_quality_status: str, outcome, action: str) -> str | None:
    if data_quality_status != "CLEAN":
        return "RECONSTRUCTION_WARNING"
    if outcome.outcome_status == "UNSCORABLE":
        # UNSCORABLE covers two distinct causes: no entry price at all, or an
        # entry price that exists but every horizon fell outside available
        # data (insufficient horizon / data gap on all four horizons).
        return "UNSCORABLE_NO_ENTRY_PRICE" if outcome.entry_price is None else "UNSCORABLE_NO_HORIZON_DATA"
    if action == "HOLD":
        return "WASH_TRADE_HOLD"
    return None


async def build_audit_rows(
    session: AsyncSession, *, broker_account_id: uuid.UUID, entries: list[WalkForwardEntry]
) -> list[AuditRow]:
    trade_repo = HistoricalTradeRepository(session)

    by_key: dict[tuple[str, date], dict[str, WalkForwardEntry]] = {}
    for entry in entries:
        key = (entry.decision.symbol, entry.decision.decision_at)
        by_key.setdefault(key, {})[entry.decision.decision_source] = entry

    total_invested_cache: dict[date, Decimal] = {}
    rows: list[AuditRow] = []

    for (symbol, decision_at), by_source in sorted(by_key.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        actual = by_source.get("ACTUAL")
        if actual is None:
            continue  # run_walk_forward always freezes ACTUAL; defensive only
        hold = by_source.get("HOLD_BASELINE")
        d, o = actual.decision, actual.outcome
        hold_o = hold.outcome if hold else None

        earliest = await trade_repo.earliest_date_for_symbol(broker_account_id, symbol, decision_at)
        holding_age_days = (decision_at - earliest).days if earliest is not None else None

        if decision_at not in total_invested_cache:
            feature_cutoff_at = datetime.combine(decision_at, datetime.max.time())
            snapshot = await get_portfolio_as_of(
                session,
                broker_account_id=broker_account_id,
                as_of_date=decision_at,
                feature_cutoff_at=feature_cutoff_at,
            )
            total_invested_cache[decision_at] = snapshot.invested_capital_total
        total_invested = total_invested_cache[decision_at]
        concentration_pct = (d.invested_capital / total_invested) if total_invested else None

        exclusion_reason = _exclusion_reason(d.data_quality_status, o, d.action)

        rows.append(
            AuditRow(
                symbol=symbol,
                decision_at=decision_at,
                decision_id=d.id,
                hold_decision_id=hold.decision.id if hold else None,
                action=d.action,
                data_quality_status=d.data_quality_status,
                reconstruction_warnings=d.reconstruction_warnings,
                outcome_status=o.outcome_status,
                quantity_held=d.quantity_held,
                invested_capital=d.invested_capital,
                hold_quantity_held=hold.decision.quantity_held if hold else None,
                holding_age_days=holding_age_days,
                concentration_pct=concentration_pct,
                calendar_year=decision_at.year,
                entry_price=o.entry_price,
                stock_return=_horizon_dict(o, "stock_return"),
                benchmark_return=_horizon_dict(o, "benchmark_return"),
                excess_return=_horizon_dict(o, "excess_return"),
                benchmark_tri_return=_horizon_dict(o, "benchmark_tri_return"),
                excess_return_tri=_horizon_dict(o, "excess_return_tri"),
                hold_stock_return=_horizon_dict(hold_o, "stock_return"),
                max_drawdown_pct=o.max_drawdown_pct,
                data_quality_notes=o.data_quality_notes,
                included_in_aggregate=exclusion_reason is None,
                exclusion_reason=exclusion_reason,
                trade_evidence=d.evidence,
            )
        )

    return rows
