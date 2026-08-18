from __future__ import annotations

"""Schemas for corporate_actions and the dividend/result calendars.

This is a distinct module from the older, orphaned schemas/events.py
(CorporateEventCreate/Read, never wired to any table). Those are left alone
per "don't break Phase 1/2 tests" — this module backs the live
corporate_actions table instead.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from investing_agent.schemas.common import BaseSchema, TimestampedSchema

ActionType = Literal[
    "dividend", "bonus", "split", "buyback", "rights", "agm", "board_meeting", "other"
]
DividendType = Literal["final", "interim", "special"]


class CorporateActionCreate(BaseSchema):
    """Internal, used by the normalization/ingestion service."""

    company_id: uuid.UUID
    symbol: str
    action_type: ActionType
    announced_date: date | None = None
    event_date: date
    ex_date: date | None = None
    record_date: date | None = None
    payment_date: date | None = None  # never inferred
    agm_date: date | None = None
    amount: Decimal | None = None
    amount_currency: str | None = "INR"
    ratio: str | None = None
    dividend_type: DividendType | None = None
    details: dict[str, Any] | None = None
    is_confirmed: bool = True
    board_meeting_announced_at: datetime | None = None
    board_meeting_date: date | None = None
    expected_result_date: date | None = None
    actual_result_published_at: datetime | None = None
    source_type: str
    source_url: str | None = None
    published_at: datetime | None = None
    data_category: str = "fact"
    confidence: float | None = None
    source_document_id: uuid.UUID | None = None
    verification_status: str = "unverified"
    verification_document_id: uuid.UUID | None = None
    verified_at: datetime | None = None
    verification_method: str | None = None
    verification_notes: str | None = None


class CorporateActionRead(TimestampedSchema):
    id: uuid.UUID
    company_id: uuid.UUID
    symbol: str
    action_type: str
    announced_date: date | None
    event_date: date
    ex_date: date | None
    record_date: date | None
    payment_date: date | None
    agm_date: date | None
    amount: Decimal | None
    amount_currency: str | None
    ratio: str | None
    dividend_type: str | None
    is_confirmed: bool
    board_meeting_announced_at: datetime | None
    board_meeting_date: date | None
    expected_result_date: date | None
    actual_result_published_at: datetime | None
    cross_checked: bool
    version: int
    is_latest: bool
    source_type: str
    source_url: str | None
    published_at: datetime | None
    available_at: datetime
    data_category: str
    verification_status: str
    verification_method: str | None
    verified_at: datetime | None


class DividendCalendarItem(BaseSchema):
    """Compact row for GET /calendar/dividends."""

    symbol: str
    company_id: uuid.UUID
    dividend_type: str | None
    amount: Decimal | None
    announced_date: date | None
    ex_date: date | None
    record_date: date | None
    payment_date: date | None
    days_to_ex_date: int | None
    verification_status: str
    is_in_portfolio: bool = False


class ResultCalendarItem(BaseSchema):
    """Compact row for GET /calendar/results."""

    symbol: str
    company_id: uuid.UUID
    board_meeting_announced_at: datetime | None
    board_meeting_date: date | None
    expected_result_date: date | None
    actual_result_published_at: datetime | None
    days_to_expected_result: int | None
    verification_status: str
    is_in_portfolio: bool = False
