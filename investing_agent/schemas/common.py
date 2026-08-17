from __future__ import annotations
"""Shared base schemas."""

import uuid
from datetime import datetime
from enum import Enum

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


class DataCategory(str, Enum):
    """Distinguishes the epistemic status of a piece of information.

    FACT      — verified from exchange/company filing (Tier 1 primary source).
                "BEL reported revenue of ₹4,200 crore in Q2 FY27."
    ESTIMATE  — model output, analyst projection, or calculated value.
                "Agent estimates Q2 PAT ₹300–340 crore."
    OPINION   — brokerage rating, TV commentary, social media view.
                "Nuvama initiates BEL with BUY, target ₹260."

    CRITICAL: these three categories must NEVER be made interchangeable.
    An ESTIMATE must not be stored as a FACT.
    An OPINION must not override a FACT.
    """
    FACT = "fact"
    ESTIMATE = "estimate"
    OPINION = "opinion"


class EvidenceItem(BaseSchema):
    """Single evidence citation attached to a recommendation or data record."""
    source: str
    published_at: datetime | None = None
    url: str | None = None
    title: str | None = None
    tier: int  # 1=exchange/filing, 2=brokerage/press, 3=TV/social
    category: DataCategory = DataCategory.FACT
    excerpt: str | None = None
    is_confirmed: bool = True  # False = rumour/unconfirmed
