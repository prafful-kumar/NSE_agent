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
