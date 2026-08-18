from __future__ import annotations

"""Schemas for Phase 5B point-in-time backtesting: one BacktestScore per
scored EstimateRun.

BacktestScoreCreate is the output of services/backtesting/scoring.py's pure
scoring function — every field on it is Python-computed from an EstimateRun's
already-persisted numbers and the actual FinancialResult, never re-derived
from an LLM.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from investing_agent.schemas.common import BaseSchema, TimestampedSchema

SurpriseDirection = Literal["beat", "inline", "miss"]
ConfidenceBucket = Literal["low", "medium", "high"]
BacktestStatus = Literal["SCORED", "UNSCORABLE"]
UnscorableReason = Literal["NO_VERIFIED_HISTORY", "NO_HISTORY_AVAILABLE"]


class BacktestScoreCreate(BaseSchema):
    company_id: uuid.UUID
    financial_period_id: uuid.UUID
    estimate_run_id: uuid.UUID
    financial_result_id: uuid.UUID
    cutoff_at: datetime
    model_version: str
    revenue_error_pct: Decimal | None = None
    pat_error_pct: Decimal | None = None
    eps_error_pct: Decimal | None = None
    margin_error_bps: Decimal | None = None
    within_band_revenue: bool | None = None
    within_band_pat: bool | None = None
    within_band_eps: bool | None = None
    surprise_direction: SurpriseDirection | None = None
    growth_direction_correct: bool | None = None
    confidence_bucket: ConfidenceBucket | None = None
    status: BacktestStatus = "SCORED"
    unscorable_reason: UnscorableReason | None = None
    verified_history_count: int = 0
    unverified_history_count: int = 0


class BacktestScoreRead(TimestampedSchema):
    id: uuid.UUID
    company_id: uuid.UUID
    financial_period_id: uuid.UUID
    estimate_run_id: uuid.UUID
    financial_result_id: uuid.UUID
    cutoff_at: datetime
    model_version: str
    revenue_error_pct: Decimal | None
    pat_error_pct: Decimal | None
    eps_error_pct: Decimal | None
    margin_error_bps: Decimal | None
    within_band_revenue: bool | None
    within_band_pat: bool | None
    within_band_eps: bool | None
    surprise_direction: str | None
    growth_direction_correct: bool | None
    confidence_bucket: str | None
    status: str
    unscorable_reason: str | None
    verified_history_count: int
    unverified_history_count: int


class BacktestDiagnostic(BaseSchema):
    """One row per period run_backtest looked at — including periods that
    never produced a BacktestScore at all (no actual available_at yet).
    Not persisted; CLI-display only, see cli.py's run-backtest command."""

    period_id: uuid.UUID
    period_label: str
    actual_available_at: datetime | None
    actual_ingested_at: datetime | None
    cutoff_at: datetime | None
    verified_history_count: int
    unverified_history_count: int
    estimate_status: Literal["SCORED", "UNSCORABLE", "SKIPPED"]
    reason: str | None
