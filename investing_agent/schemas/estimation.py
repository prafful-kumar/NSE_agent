from __future__ import annotations

"""Schemas for Phase 5A earnings estimation: the frozen FeatureSnapshot
payload the deterministic estimator (and optional LLM narrator) consumes,
and the EstimateRun it produces.

FeatureSnapshotPayload is deliberately a closed set of typed points, not a
free-form dict — every field here traces back to a specific PIT-filtered
repository query in services/estimation/features.py, so it's obvious what
data an estimate did and didn't see.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from investing_agent.schemas.common import BaseSchema, TimestampedSchema

AssumptionSource = Literal["deterministic", "llm"]


class AssumptionItem(BaseSchema):
    text: str
    source: AssumptionSource


# ── FeatureSnapshot payload ──────────────────────────────────────────────

class TargetPeriodInfo(BaseSchema):
    fiscal_year: int
    quarter: str | None
    period_type: str
    period_end: date
    label: str


class HistoricalFinancialPoint(BaseSchema):
    fiscal_year: int
    quarter: str | None
    period_type: str
    period_end: date
    statement_scope: str
    revenue: Decimal | None = None
    ebitda_margin_pct: Decimal | None = None
    pat: Decimal | None = None
    eps_diluted: Decimal | None = None


class OrderBookPoint(BaseSchema):
    as_of_date: date
    order_book_value: Decimal
    currency: str
    unit_scale: str
    book_to_bill_ratio: Decimal | None = None
    expected_execution_period: str | None = None


class GuidancePoint(BaseSchema):
    fiscal_year: int
    guidance_type: str
    metric_label: str
    guidance_value_text: str
    guidance_low: Decimal | None = None
    guidance_high: Decimal | None = None
    period_label: str | None = None


class SegmentMetricPoint(BaseSchema):
    segment_name: str
    metric_type: str
    value: Decimal
    unit_scale: str


class OperationalMetricPoint(BaseSchema):
    metric_name: str
    value: Decimal
    unit: str | None = None
    as_of_date: date | None = None


class CatalystPoint(BaseSchema):
    description: str
    catalyst_type: str
    expected_by: str | None = None


class RiskPoint(BaseSchema):
    description: str
    risk_type: str
    severity: str


class NewsSignalPoint(BaseSchema):
    headline: str
    first_seen_at: datetime
    impact_classification: dict | None = None
    rationale: str | None = None


class FeatureSnapshotPayload(BaseSchema):
    target_period: TargetPeriodInfo
    historical_financials: list[HistoricalFinancialPoint] = []
    order_book: OrderBookPoint | None = None
    guidance: list[GuidancePoint] = []
    segment_metrics: list[SegmentMetricPoint] = []
    operational_metrics: list[OperationalMetricPoint] = []
    active_catalysts: list[CatalystPoint] = []
    active_risks: list[RiskPoint] = []
    recent_news: list[NewsSignalPoint] = []
    used_unresolved_scope: bool = False


class FeatureSnapshotCreate(BaseSchema):
    company_id: uuid.UUID
    financial_period_id: uuid.UUID
    cutoff_at: datetime
    payload: dict


class FeatureSnapshotRead(TimestampedSchema):
    id: uuid.UUID
    company_id: uuid.UUID
    financial_period_id: uuid.UUID
    cutoff_at: datetime
    payload: dict


# ── EstimateRun ───────────────────────────────────────────────────────────

class EstimateRunCreate(BaseSchema):
    company_id: uuid.UUID
    financial_period_id: uuid.UUID
    cutoff_at: datetime
    model_version: str
    feature_snapshot_id: uuid.UUID
    revenue_low: Decimal | None = None
    revenue_base: Decimal | None = None
    revenue_high: Decimal | None = None
    ebitda_margin_low: Decimal | None = None
    ebitda_margin_base: Decimal | None = None
    ebitda_margin_high: Decimal | None = None
    pat_low: Decimal | None = None
    pat_base: Decimal | None = None
    pat_high: Decimal | None = None
    eps_low: Decimal | None = None
    eps_base: Decimal | None = None
    eps_high: Decimal | None = None
    confidence: Decimal | None = None
    assumptions: list[AssumptionItem] = []


class EstimateRunRead(TimestampedSchema):
    id: uuid.UUID
    company_id: uuid.UUID
    financial_period_id: uuid.UUID
    cutoff_at: datetime
    model_version: str
    feature_snapshot_id: uuid.UUID
    revenue_low: Decimal | None
    revenue_base: Decimal | None
    revenue_high: Decimal | None
    ebitda_margin_low: Decimal | None
    ebitda_margin_base: Decimal | None
    ebitda_margin_high: Decimal | None
    pat_low: Decimal | None
    pat_base: Decimal | None
    pat_high: Decimal | None
    eps_low: Decimal | None
    eps_base: Decimal | None
    eps_high: Decimal | None
    confidence: Decimal | None
    assumptions: list[dict]
