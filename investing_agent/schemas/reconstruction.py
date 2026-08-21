from __future__ import annotations

"""Domain schemas for portfolio reconstruction (Phase 6B).

A PortfolioSnapshotAsOf answers "what did my portfolio look like on date
X" by replaying HistoricalTrade/HistoricalCashFlow facts (Phase 6A) through
a FIFO lot engine. Two fields are deliberately never given false precision:

- cash_balance_partial always carries cash_balance_caveat +
  cash_balance_ledger_coverage_start, because the cash-flow ledger only
  covers a fraction of the trade history (see replay.py) -- there is no
  "EXACT" cash figure to report.
- unrealized_pnl / price_at_date are None with price_source="UNAVAILABLE"
  whenever no historical price is known for that symbol/date, rather than
  an interpolated or fabricated value.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from investing_agent.schemas.broker_history import StrategyProfile
from investing_agent.schemas.common import BaseSchema

PriceSource = Literal["STATEMENT_DERIVED", "UNAVAILABLE"]


class PositionAsOf(BaseSchema):
    symbol: str
    isin: str | None = None
    company_id: uuid.UUID | None = None
    quantity_held: Decimal
    average_cost: Decimal
    invested_capital: Decimal
    price_at_date: Decimal | None = None
    price_source: PriceSource = "UNAVAILABLE"
    unrealized_pnl: Decimal | None = None
    realized_pnl_cumulative: Decimal
    open_lot_count: int
    corporate_action_flag: bool = False


class AppliedCorporateActionAdjustment(BaseSchema):
    """Audit record of a split/bonus's effect on this account's open lots
    at the moment it was applied during replay -- see fifo.py's
    CorporateActionAdjustmentApplied."""

    symbol: str
    event_type: str
    event_date: date
    ratio_new: Decimal
    ratio_old: Decimal
    old_quantity: Decimal
    new_quantity: Decimal
    old_cost_per_share: Decimal
    adjusted_cost_per_share: Decimal
    source: str


class PortfolioSnapshotAsOf(BaseSchema):
    broker_account_id: uuid.UUID
    as_of_date: date
    strategy_profile: StrategyProfile
    positions: list[PositionAsOf]
    cash_balance_partial: Decimal
    cash_balance_caveat: str
    cash_balance_ledger_coverage_start: date | None
    invested_capital_total: Decimal
    unrealized_pnl_total: Decimal | None
    realized_pnl_cumulative_total: Decimal
    warnings: list[str] = []
    corporate_action_adjustments: list[AppliedCorporateActionAdjustment] = []


class ReconstructionRunResult(BaseSchema):
    broker_account_id: uuid.UUID
    as_of_date: date
    lots_written: int
    symbols_with_open_positions: int
    warnings: list[str] = []
    snapshot: PortfolioSnapshotAsOf


class ReconciliationRowDiff(BaseSchema):
    statement_file: str
    symbol: str
    field: str
    expected: Decimal
    actual: Decimal
    delta: Decimal
    likely_cause: str | None = None


class ReconciliationQualityScore(BaseSchema):
    """A single trustworthiness readout for the reconstruction, computed at
    symbol granularity (a symbol counts as "matched" on a field only if
    every statement row for it agreed within tolerance). None means the
    field was never checked (no statement rows touched it) -- distinct
    from 0%, which means it was checked and always wrong."""

    holdings_quantity_match_pct: float | None
    holdings_avg_cost_match_pct: float | None
    realized_pnl_match_pct: float | None
    unresolved_symbol_count: int


class ReconciliationReport(BaseSchema):
    broker_account_id: uuid.UUID
    overall_status: Literal["CLEAN", "DIVERGENT"]
    total_symbols_checked: int
    divergent_symbols: list[str]
    expected_gaps_corporate_action: list[str]
    row_diffs: list[ReconciliationRowDiff]
    warnings: list[str] = []
    quality_score: ReconciliationQualityScore
