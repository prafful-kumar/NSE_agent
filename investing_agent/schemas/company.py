from __future__ import annotations
import uuid
from typing import Any

from pydantic import Field

from investing_agent.schemas.common import BaseSchema, TimestampedSchema


class CompanyCreate(BaseSchema):
    symbol: str = Field(..., min_length=1, max_length=30)
    isin: str | None = Field(None, min_length=12, max_length=12)
    name: str = Field(..., min_length=1, max_length=255)
    exchange: str = Field(..., pattern="^(NSE|BSE)$")
    sector: str | None = None
    industry: str | None = None
    market_cap_category: str | None = Field(None, pattern="^(Large|Mid|Small)$")
    is_active: bool = True
    metadata_: dict[str, Any] | None = Field(None, alias="metadata")

    model_config = {"populate_by_name": True}


class CompanyRead(TimestampedSchema):
    id: uuid.UUID
    symbol: str
    isin: str | None
    name: str
    exchange: str
    sector: str | None
    industry: str | None
    market_cap_category: str | None
    is_active: bool
