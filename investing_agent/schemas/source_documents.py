from __future__ import annotations

"""Schemas for the raw source-document archive (Tier-1 ground truth).

SourceDocumentRead mirrors db.models.SourceDocument. Nothing here infers or
guesses fields the source didn't provide — filing_type/document_type default
to "other"/best-effort at the adapter layer, never invented here.
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field

from investing_agent.schemas.common import BaseSchema, TimestampedSchema

FilingType = Literal[
    "quarterly_result",
    "annual_report",
    "investor_presentation",
    "announcement",
    "concall_transcript",
    "other",
]
DocumentType = Literal["pdf", "html", "xml", "xbrl", "json", "txt"]


class SourceDocumentCreate(BaseSchema):
    company_id: uuid.UUID
    symbol: str = Field(..., min_length=1, max_length=30)
    exchange: str = Field(..., pattern="^(NSE|BSE|IR)$")
    filing_type: FilingType
    document_type: DocumentType
    title: str
    period_id: uuid.UUID | None = None
    content_hash: str = Field(..., min_length=64, max_length=64)
    storage_path: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    source_type: str
    source_url: str | None = None
    published_at: datetime | None = None
    data_category: str = "fact"
    confidence: float | None = None


class SourceDocumentRead(TimestampedSchema):
    id: uuid.UUID
    company_id: uuid.UUID
    symbol: str
    exchange: str
    filing_type: str
    document_type: str
    title: str
    period_id: uuid.UUID | None
    content_hash: str
    storage_path: str | None
    mime_type: str | None
    size_bytes: int | None
    is_current: bool
    source_type: str
    source_url: str | None
    published_at: datetime | None
    available_at: datetime
    ingested_at: datetime
    data_category: str


class DocumentVersionRead(TimestampedSchema):
    id: uuid.UUID
    source_document_id: uuid.UUID
    version_number: int
    status: str
    effective_from: date | None
    superseded_by_id: uuid.UUID | None
    notes: str | None
