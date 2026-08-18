from __future__ import annotations

"""Schemas for Phase 3B primary-source company intelligence: order book,
management guidance, segment/operational metrics, capacity updates,
management commentary.

Every *Create schema requires source_document_id — these facts are only ever
entered citing an archived document, never floated free of provenance.
extraction_method defaults to MANUAL (CLI/human entry); LLM_ASSISTED exists
in the vocabulary for a future extractor but nothing in Phase 3B sets it on
these tables (see ExtractionCandidate in db.models for where LLM output must
land instead).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from investing_agent.schemas.common import BaseSchema, TimestampedSchema

ExtractionMethod = Literal["MANUAL", "DETERMINISTIC", "LLM_ASSISTED", "HYBRID"]
ExtractionVerificationStatus = Literal[
    "UNVERIFIED", "HUMAN_VERIFIED", "DOCUMENT_VERIFIED", "REJECTED"
]


class _ExtractionFields(BaseSchema):
    source_document_id: uuid.UUID
    source_page: int | None = None
    source_section: str | None = None
    source_quote: str | None = None
    extracted_at: datetime | None = None
    extraction_method: ExtractionMethod = "MANUAL"
    extractor_version: str | None = None
    verification_status: ExtractionVerificationStatus = "UNVERIFIED"
    verified_at: datetime | None = None
    verified_by: str | None = None


class _ExtractionFieldsRead(BaseSchema):
    source_document_id: uuid.UUID
    source_page: int | None
    source_section: str | None
    source_quote: str | None
    extracted_at: datetime | None
    extraction_method: str
    extractor_version: str | None
    verification_status: str
    verified_at: datetime | None
    verified_by: str | None


class _ProvenanceFields(BaseSchema):
    source_type: str
    source_url: str | None = None
    published_at: datetime | None = None
    data_category: str = "fact"
    confidence: float | None = None


class _ProvenanceFieldsRead(BaseSchema):
    source_type: str
    source_url: str | None
    published_at: datetime | None
    available_at: datetime
    data_category: str
    confidence: Decimal | None


# ── Order book ────────────────────────────────────────────────────────────

class OrderBookSnapshotCreate(_ExtractionFields, _ProvenanceFields):
    company_id: uuid.UUID
    symbol: str
    as_of_date: date
    order_book_value: Decimal
    currency: str = "INR"
    unit_scale: str = "UNRESOLVED"
    segment: str | None = None
    book_to_bill_ratio: Decimal | None = None
    expected_execution_period: str | None = None
    notes: str | None = None


class OrderBookSnapshotRead(TimestampedSchema, _ExtractionFieldsRead, _ProvenanceFieldsRead):
    id: uuid.UUID
    company_id: uuid.UUID
    symbol: str
    as_of_date: date
    order_book_value: Decimal
    currency: str
    unit_scale: str
    segment: str | None
    book_to_bill_ratio: Decimal | None
    expected_execution_period: str | None
    notes: str | None


# ── Management guidance ──────────────────────────────────────────────────

class ManagementGuidanceCreate(_ExtractionFields, _ProvenanceFields):
    company_id: uuid.UUID
    symbol: str
    fiscal_year: int
    guidance_type: str
    metric_label: str
    guidance_value_text: str
    guidance_low: Decimal | None = None
    guidance_high: Decimal | None = None
    period_label: str | None = None
    given_by: str | None = None
    context: str | None = None


class ManagementGuidanceRead(TimestampedSchema, _ExtractionFieldsRead, _ProvenanceFieldsRead):
    id: uuid.UUID
    company_id: uuid.UUID
    symbol: str
    fiscal_year: int
    guidance_type: str
    metric_label: str
    guidance_value_text: str
    guidance_low: Decimal | None
    guidance_high: Decimal | None
    period_label: str | None
    given_by: str | None
    context: str | None


# ── Segment metrics ───────────────────────────────────────────────────────

class SegmentMetricCreate(_ExtractionFields, _ProvenanceFields):
    company_id: uuid.UUID
    symbol: str
    period_id: uuid.UUID | None = None
    segment_name: str
    metric_type: str
    value: Decimal
    unit_scale: str = "UNRESOLVED"
    currency: str = "INR"


class SegmentMetricRead(TimestampedSchema, _ExtractionFieldsRead, _ProvenanceFieldsRead):
    id: uuid.UUID
    company_id: uuid.UUID
    symbol: str
    period_id: uuid.UUID | None
    segment_name: str
    metric_type: str
    value: Decimal
    unit_scale: str
    currency: str


# ── Operational metrics ──────────────────────────────────────────────────

class OperationalMetricCreate(_ExtractionFields, _ProvenanceFields):
    company_id: uuid.UUID
    symbol: str
    period_id: uuid.UUID | None = None
    metric_name: str
    value: Decimal
    unit: str | None = None
    as_of_date: date | None = None


class OperationalMetricRead(TimestampedSchema, _ExtractionFieldsRead, _ProvenanceFieldsRead):
    id: uuid.UUID
    company_id: uuid.UUID
    symbol: str
    period_id: uuid.UUID | None
    metric_name: str
    value: Decimal
    unit: str | None
    as_of_date: date | None


# ── Capacity updates ─────────────────────────────────────────────────────

class CapacityUpdateCreate(_ExtractionFields, _ProvenanceFields):
    company_id: uuid.UUID
    symbol: str
    update_type: str
    location: str | None = None
    capacity_before: Decimal | None = None
    capacity_after: Decimal | None = None
    unit: str | None = None
    announced_date: date | None = None
    expected_completion: str | None = None
    description: str | None = None


class CapacityUpdateRead(TimestampedSchema, _ExtractionFieldsRead, _ProvenanceFieldsRead):
    id: uuid.UUID
    company_id: uuid.UUID
    symbol: str
    update_type: str
    location: str | None
    capacity_before: Decimal | None
    capacity_after: Decimal | None
    unit: str | None
    announced_date: date | None
    expected_completion: str | None
    description: str | None


# ── Management commentary ────────────────────────────────────────────────

class ManagementCommentaryCreate(_ExtractionFields, _ProvenanceFields):
    company_id: uuid.UUID
    symbol: str
    period_id: uuid.UUID | None = None
    speaker: str | None = None
    topic: str | None = None
    quote: str
    context: str | None = None


class ManagementCommentaryRead(TimestampedSchema, _ExtractionFieldsRead, _ProvenanceFieldsRead):
    id: uuid.UUID
    company_id: uuid.UUID
    symbol: str
    period_id: uuid.UUID | None
    speaker: str | None
    topic: str | None
    quote: str
    context: str | None
