from __future__ import annotations
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from investing_agent.schemas.common import BaseSchema, SourcedSchema, TimestampedSchema


class HoldingRead(BaseSchema):
    id: uuid.UUID
    symbol: str
    isin: str | None
    quantity: int
    average_price: Decimal
    last_price: Decimal | None
    current_value: Decimal | None
    pnl: Decimal | None
    pnl_pct: Decimal | None
    portfolio_weight_pct: Decimal | None
    t1_quantity: int | None


class PortfolioSnapshotRead(TimestampedSchema, SourcedSchema):
    id: uuid.UUID
    user_id: str
    snapshot_date: date
    total_value: Decimal | None
    total_invested: Decimal | None
    total_pnl: Decimal | None
    pnl_pct: Decimal | None
    holdings: list[HoldingRead] = []


class WatchlistItemRead(TimestampedSchema):
    id: uuid.UUID
    user_id: str
    symbol: str
    reason: str | None
    is_active: bool


class WatchlistItemCreate(BaseSchema):
    symbol: str = Field(..., min_length=1, max_length=30)
    reason: str | None = None


# ── Broker-layer data transfer objects ────────────────────────────────────────
# Used between BrokerGateway and the portfolio node.
# These are NOT ORM models — they are wire-format DTOs.

class BrokerHolding(BaseSchema):
    """Holding as returned by the broker (Zerodha Kite MCP)."""
    tradingsymbol: str
    isin: str | None = None
    exchange: str
    quantity: int
    t1_quantity: int = 0
    average_price: Decimal
    last_price: Decimal
    pnl: Decimal
    close_price: Decimal | None = None
    product: str | None = None  # CNC | MIS | etc.


class BrokerPortfolioResponse(BaseSchema):
    """Full portfolio response from the broker."""
    holdings: list[BrokerHolding]
    fetched_at: datetime
    source: str = "zerodha_kite_mcp"
