from __future__ import annotations

"""Instrument identity schemas.

ISIN is the durable identity for equities.  Zerodha instrument tokens and
NSE/BSE trading symbols can change (delisting, relisting, symbol changes).
The instrument_master table maps all of these to our internal company_id.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from investing_agent.schemas.common import BaseSchema, TimestampedSchema

InstrumentType = Literal["EQ", "ETF", "MF", "BE", "BL", "GB", "GS", "INDEX", "OTHER"]


class InstrumentCreate(BaseSchema):
    tradingsymbol: str = Field(..., min_length=1, max_length=50)
    exchange: str = Field(..., pattern="^(NSE|BSE)$")
    isin: str | None = Field(None, min_length=12, max_length=12)
    instrument_type: InstrumentType = "EQ"
    zerodha_instrument_token: int | None = None
    company_id: uuid.UUID | None = None


class InstrumentRead(TimestampedSchema):
    id: uuid.UUID
    tradingsymbol: str
    exchange: str
    isin: str | None
    instrument_type: str
    zerodha_instrument_token: int | None
    company_id: uuid.UUID | None
    is_active: bool
    last_seen: datetime | None


class ResolvedInstrument(BaseSchema):
    """Result of resolving a broker holding to our internal identity."""
    tradingsymbol: str
    exchange: str
    isin: str | None
    instrument_type: str
    company_id: uuid.UUID | None
    company_symbol: str | None
    resolution_method: str  # "isin" | "symbol_exchange" | "created_new" | "unresolved"
