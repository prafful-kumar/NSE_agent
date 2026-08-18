from __future__ import annotations

"""Schemas for Phase 4A relational research memory: research_notes,
thesis_changes, catalysts, risk_observations, source_reliability.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from investing_agent.schemas.common import BaseSchema, TimestampedSchema


# ── Research notes ────────────────────────────────────────────────────────

class ResearchNoteCreate(BaseSchema):
    company_id: uuid.UUID
    note_type: str = "manual"
    text: str
    effective_at: datetime
    source_document_id: uuid.UUID | None = None
    news_event_id: uuid.UUID | None = None
    created_by: str


class ResearchNoteRead(TimestampedSchema):
    id: uuid.UUID
    company_id: uuid.UUID
    note_type: str
    text: str
    effective_at: datetime
    source_document_id: uuid.UUID | None
    news_event_id: uuid.UUID | None
    created_by: str


# ── Thesis changes ────────────────────────────────────────────────────────

class ThesisChangeCreate(BaseSchema):
    company_id: uuid.UUID
    thesis_id: uuid.UUID | None = None
    change_type: str
    reason: str
    evidence_news_event_id: uuid.UUID | None = None
    evidence_research_note_id: uuid.UUID | None = None
    effective_at: datetime


class ThesisChangeRead(TimestampedSchema):
    id: uuid.UUID
    company_id: uuid.UUID
    thesis_id: uuid.UUID | None
    change_type: str
    reason: str
    evidence_news_event_id: uuid.UUID | None
    evidence_research_note_id: uuid.UUID | None
    effective_at: datetime


# ── Catalysts ─────────────────────────────────────────────────────────────

class CatalystCreate(BaseSchema):
    company_id: uuid.UUID
    description: str
    catalyst_type: str = "other"
    status: str = "active"
    expected_by: str | None = None
    news_event_id: uuid.UUID | None = None
    source_document_id: uuid.UUID | None = None


class CatalystRead(TimestampedSchema):
    id: uuid.UUID
    company_id: uuid.UUID
    description: str
    catalyst_type: str
    status: str
    expected_by: str | None
    news_event_id: uuid.UUID | None
    source_document_id: uuid.UUID | None


# ── Risk observations ─────────────────────────────────────────────────────

class RiskObservationCreate(BaseSchema):
    company_id: uuid.UUID
    description: str
    risk_type: str = "other"
    severity: str = "unclassified"
    status: str = "active"
    news_event_id: uuid.UUID | None = None
    source_document_id: uuid.UUID | None = None


class RiskObservationRead(TimestampedSchema):
    id: uuid.UUID
    company_id: uuid.UUID
    description: str
    risk_type: str
    severity: str
    status: str
    news_event_id: uuid.UUID | None
    source_document_id: uuid.UUID | None


# ── Source reliability ────────────────────────────────────────────────────

class SourceReliabilityRead(TimestampedSchema):
    id: uuid.UUID
    source_name: str
    enabled: bool
    research_only: bool
    successful_fetches: int
    failed_fetches: int
    stale_feed_count: int
    duplicate_rate: Decimal | None
    company_relevance_rate: Decimal | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
