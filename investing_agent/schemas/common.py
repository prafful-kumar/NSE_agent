from __future__ import annotations
"""Shared base schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TimestampedSchema(BaseSchema):
    created_at: datetime
    updated_at: datetime


class SourcedSchema(BaseSchema):
    """For schemas that represent externally sourced data."""
    source: str
    source_timestamp: datetime | None = None
    ingestion_timestamp: datetime


class EvidenceItem(BaseSchema):
    """Single evidence citation attached to a recommendation."""
    source: str
    published_at: datetime | None = None
    url: str | None = None
    title: str | None = None
    tier: int  # 1=exchange/filing, 2=brokerage/press, 3=TV/social
    excerpt: str | None = None
