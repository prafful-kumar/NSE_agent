from __future__ import annotations

"""Schemas for Phase 6C walk-forward simulation v1.

Mirrors schemas/backtesting.py's shape/typing conventions (Literal types for
enums, Decimal | None defaults for every value that can be genuinely
unknown). WalkForwardDecision/WalkForwardOutcome have no updated_at column
(they are immutable once written), so their Read schemas are hand-rolled
rather than built on TimestampedSchema.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from investing_agent.schemas.common import BaseSchema

DecisionSource = Literal["ACTUAL", "HOLD_BASELINE", "AGENT"]
# INSUFFICIENT_EVIDENCE is valid only for the offline AGENT comparator.  It
# records that a policy declined to manufacture an action from missing PIT
# inputs; it is never interpreted as a trade.
Action = Literal["BUY", "ADD", "HOLD", "REDUCE", "EXIT", "INSUFFICIENT_EVIDENCE"]
DataQualityStatus = Literal["CLEAN", "RECONSTRUCTION_WARNING"]
OutcomeStatus = Literal["SCORED", "PARTIAL", "UNSCORABLE"]


class WalkForwardRunCreate(BaseSchema):
    broker_account_id: uuid.UUID
    strategy_profile: str
    horizons_months: list[int]
    model_version: str


class WalkForwardRunRead(BaseSchema):
    id: uuid.UUID
    broker_account_id: uuid.UUID
    strategy_profile: str
    horizons_months: list[int]
    model_version: str
    created_at: datetime
    updated_at: datetime


class WalkForwardDecisionCreate(BaseSchema):
    run_id: uuid.UUID
    broker_account_id: uuid.UUID
    company_id: uuid.UUID | None = None
    symbol: str
    decision_at: date
    feature_cutoff_at: datetime
    decision_source: DecisionSource
    action: Action
    confidence: Decimal | None = None
    reasoning: str | None = None
    evidence: list[dict[str, Any]] = []
    quantity_held: Decimal
    average_cost: Decimal
    invested_capital: Decimal
    reconstruction_warnings: list[str] = []
    data_quality_status: DataQualityStatus


class WalkForwardDecisionRead(BaseSchema):
    id: uuid.UUID
    run_id: uuid.UUID
    broker_account_id: uuid.UUID
    company_id: uuid.UUID | None
    symbol: str
    decision_at: date
    feature_cutoff_at: datetime
    decision_source: DecisionSource
    action: Action
    confidence: Decimal | None
    reasoning: str | None
    evidence: list[dict[str, Any]]
    quantity_held: Decimal
    average_cost: Decimal
    invested_capital: Decimal
    reconstruction_warnings: list[str]
    data_quality_status: DataQualityStatus
    frozen_at: datetime


class WalkForwardOutcomeCreate(BaseSchema):
    decision_id: uuid.UUID
    entry_price: Decimal | None = None
    entry_price_date: date | None = None
    price_1m: Decimal | None = None
    price_3m: Decimal | None = None
    price_6m: Decimal | None = None
    price_12m: Decimal | None = None
    price_1m_date: date | None = None
    price_3m_date: date | None = None
    price_6m_date: date | None = None
    price_12m_date: date | None = None
    stock_return_1m: Decimal | None = None
    stock_return_3m: Decimal | None = None
    stock_return_6m: Decimal | None = None
    stock_return_12m: Decimal | None = None
    benchmark_return_1m: Decimal | None = None
    benchmark_return_3m: Decimal | None = None
    benchmark_return_6m: Decimal | None = None
    benchmark_return_12m: Decimal | None = None
    excess_return_1m: Decimal | None = None
    excess_return_3m: Decimal | None = None
    excess_return_6m: Decimal | None = None
    excess_return_12m: Decimal | None = None
    benchmark_tri_return_1m: Decimal | None = None
    benchmark_tri_return_3m: Decimal | None = None
    benchmark_tri_return_6m: Decimal | None = None
    benchmark_tri_return_12m: Decimal | None = None
    excess_return_tri_1m: Decimal | None = None
    excess_return_tri_3m: Decimal | None = None
    excess_return_tri_6m: Decimal | None = None
    excess_return_tri_12m: Decimal | None = None
    max_drawdown_pct: Decimal | None = None
    outcome_status: OutcomeStatus
    data_quality_notes: list[str] = []


class WalkForwardOutcomeRead(BaseSchema):
    id: uuid.UUID
    decision_id: uuid.UUID
    entry_price: Decimal | None
    entry_price_date: date | None
    price_1m: Decimal | None
    price_3m: Decimal | None
    price_6m: Decimal | None
    price_12m: Decimal | None
    price_1m_date: date | None
    price_3m_date: date | None
    price_6m_date: date | None
    price_12m_date: date | None
    stock_return_1m: Decimal | None
    stock_return_3m: Decimal | None
    stock_return_6m: Decimal | None
    stock_return_12m: Decimal | None
    benchmark_return_1m: Decimal | None
    benchmark_return_3m: Decimal | None
    benchmark_return_6m: Decimal | None
    benchmark_return_12m: Decimal | None
    excess_return_1m: Decimal | None
    excess_return_3m: Decimal | None
    excess_return_6m: Decimal | None
    excess_return_12m: Decimal | None
    benchmark_tri_return_1m: Decimal | None
    benchmark_tri_return_3m: Decimal | None
    benchmark_tri_return_6m: Decimal | None
    benchmark_tri_return_12m: Decimal | None
    excess_return_tri_1m: Decimal | None
    excess_return_tri_3m: Decimal | None
    excess_return_tri_6m: Decimal | None
    excess_return_tri_12m: Decimal | None
    max_drawdown_pct: Decimal | None
    outcome_status: OutcomeStatus
    data_quality_notes: list[str]
    evaluated_at: datetime
