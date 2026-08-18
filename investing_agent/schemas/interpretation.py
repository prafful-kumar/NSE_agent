from __future__ import annotations

"""Schemas for Phase 4B event interpretation: event_interpretations.

ImpactClassification is intentionally a plain dict (not a nested pydantic
model per dimension) at the storage layer — it round-trips straight to/from
the JSONB column — but ImpactDimension/ImpactDirection/ImpactMagnitude give
callers (rule layer, LLM structured-output schema, CLI display) a closed
vocabulary to validate against rather than freeform strings.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from investing_agent.schemas.common import BaseSchema, TimestampedSchema

ImpactDimension = Literal[
    "revenue", "margins", "order_book", "management", "regulation",
    "capital_allocation", "valuation",
]
ImpactDirection = Literal["positive", "negative", "neutral"]
ImpactMagnitude = Literal["low", "medium", "high"]
InterpretationExtractionMethod = Literal["DETERMINISTIC", "LLM_ASSISTED"]
InterpretationReviewStatus = Literal["pending", "accepted", "rejected", "edited"]


class ImpactAssessment(BaseModel):
    direction: ImpactDirection
    magnitude: ImpactMagnitude


class EventInterpretationCreate(BaseSchema):
    news_event_id: uuid.UUID
    company_id: uuid.UUID
    impact_classification: dict[ImpactDimension, ImpactAssessment]
    rationale: str
    candidate_catalyst: dict | None = None
    candidate_risk: dict | None = None
    candidate_thesis_change: dict | None = None
    extraction_method: InterpretationExtractionMethod = "DETERMINISTIC"
    extractor_version: str | None = None
    confidence: Decimal
    review_status: InterpretationReviewStatus = "pending"


class EventInterpretationRead(TimestampedSchema):
    id: uuid.UUID
    news_event_id: uuid.UUID
    company_id: uuid.UUID
    impact_classification: dict
    rationale: str
    candidate_catalyst: dict | None
    candidate_risk: dict | None
    candidate_thesis_change: dict | None
    extraction_method: str
    extractor_version: str | None
    confidence: Decimal
    review_status: str
    reviewed_at: datetime | None
    reviewed_by: str | None
    resulting_catalyst_id: uuid.UUID | None
    resulting_risk_observation_id: uuid.UUID | None
    resulting_thesis_change_id: uuid.UUID | None
