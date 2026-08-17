from __future__ import annotations
"""Decision output contract.

Every recommendation MUST match this schema.
The action field is from a closed set — no free-form strings.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from investing_agent.schemas.common import BaseSchema, EvidenceItem, TimestampedSchema

RecommendationAction = Literal[
    "BUY", "ADD", "HOLD", "REDUCE", "AVOID", "WATCH", "INSUFFICIENT_EVIDENCE"
]

ThesisStatus = Literal["intact", "improving", "weakening", "broken", "unknown"]


class FairValueRange(BaseSchema):
    low: Decimal
    base: Decimal
    high: Decimal


class EarningsPreview(BaseSchema):
    quarter: str  # e.g. "FY27-Q2"
    revenue: dict[str, Decimal | None] | None = None  # low/base/high
    pat: dict[str, Decimal | None] | None = None
    surprise_probability: Decimal | None = Field(None, ge=0, le=1)


class RecommendationCreate(BaseSchema):
    symbol: str
    action: RecommendationAction
    horizon: str | None = None  # e.g. "12-24 months"
    confidence: Decimal | None = Field(None, ge=0, le=1)
    thesis_status: ThesisStatus | None = None
    fair_value_range: FairValueRange | None = None
    earnings_preview: EarningsPreview | None = None
    reasons: list[str] = Field(default_factory=list, max_length=10)
    risks: list[str] = Field(default_factory=list, max_length=10)
    invalidation_conditions: list[str] = Field(default_factory=list, max_length=10)
    upcoming_events: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    data_freshness: dict[str, Any] | None = None
    requires_human_review: bool = True
    prompt_version: str | None = None
    model_version: str | None = None


class RecommendationRead(TimestampedSchema):
    id: uuid.UUID
    user_id: str
    symbol: str
    action: str
    horizon: str | None
    confidence: Decimal | None
    thesis_status: str | None
    fair_value_range: FairValueRange | None = None
    reasons: list[str] | None
    risks: list[str] | None
    invalidation_conditions: list[str] | None
    upcoming_events: list[str] | None
    evidence: list[dict[str, Any]] | None
    data_freshness: dict[str, Any] | None
    requires_human_review: bool
    human_approved: bool | None
    approved_at: datetime | None
