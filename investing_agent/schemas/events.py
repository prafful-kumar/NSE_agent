from __future__ import annotations
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator

from investing_agent.schemas.common import BaseSchema, SourcedSchema, TimestampedSchema

EventType = Literal[
    "dividend",
    "ex_dividend",
    "record_date",
    "payment_date",
    "result_date",
    "agm",
    "bonus",
    "split",
    "buyback",
    "rights",
    "other",
]


class CorporateEventCreate(BaseSchema):
    symbol: str = Field(..., min_length=1, max_length=30)
    event_type: EventType
    event_date: date
    announced_date: date | None = None
    ex_date: date | None = None
    record_date: date | None = None
    # payment_date MUST come from primary source; never infer it
    payment_date: date | None = None
    amount: Decimal | None = None
    amount_currency: str = "INR"
    ratio: str | None = None
    details: dict[str, Any] | None = None
    source: str
    source_url: str | None = None
    source_timestamp: datetime | None = None
    is_confirmed: bool = True


class CorporateEventRead(TimestampedSchema, SourcedSchema):
    id: uuid.UUID
    company_id: uuid.UUID | None
    symbol: str
    event_type: str
    event_date: date
    announced_date: date | None
    ex_date: date | None
    record_date: date | None
    payment_date: date | None
    amount: Decimal | None
    amount_currency: str | None
    ratio: str | None
    details: dict[str, Any] | None
    source_url: str | None
    is_confirmed: bool


class UpcomingEventSummary(BaseSchema):
    """Compact event for calendar views (next 7/30/90 days)."""
    symbol: str
    event_type: str
    event_date: date
    amount: Decimal | None
    days_away: int
    is_in_portfolio: bool
    is_on_watchlist: bool
