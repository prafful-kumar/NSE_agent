from __future__ import annotations
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from investing_agent.schemas.common import BaseSchema, TimestampedSchema

ThesisStatus = Literal["active", "watching", "exited", "avoided"]


class InvestmentThesisCreate(BaseSchema):
    symbol: str = Field(..., min_length=1, max_length=30)
    status: ThesisStatus = "watching"
    thesis: str | None = None
    buy_reasons: list[str] | None = None
    risk_factors: list[str] | None = None
    catalysts: list[str] | None = None
    invalidation_conditions: list[str] | None = None
    target_price_low: Decimal | None = None
    target_price_base: Decimal | None = None
    target_price_high: Decimal | None = None
    horizon_months: int | None = Field(None, ge=1, le=360)
    entry_price: Decimal | None = None


class InvestmentThesisUpdate(BaseSchema):
    status: ThesisStatus | None = None
    thesis: str | None = None
    buy_reasons: list[str] | None = None
    risk_factors: list[str] | None = None
    catalysts: list[str] | None = None
    invalidation_conditions: list[str] | None = None
    target_price_low: Decimal | None = None
    target_price_base: Decimal | None = None
    target_price_high: Decimal | None = None
    horizon_months: int | None = Field(None, ge=1, le=360)
    exit_price: Decimal | None = None
    outcome_notes: str | None = None


class InvestmentThesisRead(TimestampedSchema):
    id: uuid.UUID
    user_id: str
    company_id: uuid.UUID | None
    symbol: str
    status: str
    thesis: str | None
    buy_reasons: list[str] | None
    risk_factors: list[str] | None
    catalysts: list[str] | None
    invalidation_conditions: list[str] | None
    target_price_low: Decimal | None
    target_price_base: Decimal | None
    target_price_high: Decimal | None
    horizon_months: int | None
    entry_price: Decimal | None
    exit_price: Decimal | None
    outcome_notes: str | None
