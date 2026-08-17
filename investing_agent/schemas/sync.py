from __future__ import annotations

"""Sync result schemas — output of PortfolioSyncService."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from investing_agent.schemas.common import BaseSchema


class HoldingNormalized(BaseSchema):
    """Single holding after Zerodha normalization + instrument resolution."""
    tradingsymbol: str
    exchange: str
    isin: str | None
    instrument_type: str
    company_id: uuid.UUID | None
    quantity: int
    t1_quantity: int
    average_price: Decimal
    last_price: Decimal
    close_price: Decimal | None
    current_value: Decimal
    invested_value: Decimal
    pnl: Decimal
    pnl_pct: Decimal
    day_change: Decimal | None
    day_change_pct: Decimal | None
    portfolio_weight_pct: Decimal
    product: str | None
    resolution_method: str  # how instrument was resolved


class ReconciliationWarning(BaseSchema):
    """Non-fatal issue found during sync — shown to user but doesn't fail sync."""
    code: str
    symbol: str | None
    message: str
    data: dict[str, Any] | None = None


class SyncResult(BaseSchema):
    """Full result from PortfolioSyncService.sync()."""
    snapshot_id: uuid.UUID
    user_id: str
    sync_timestamp: datetime
    source: str
    holdings_count: int
    total_value: Decimal
    total_invested: Decimal
    total_pnl: Decimal
    pnl_pct: Decimal
    holdings: list[HoldingNormalized]
    warnings: list[ReconciliationWarning] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    ingestion_run_id: uuid.UUID | None = None
    is_stale: bool = False
    data_timestamp: datetime | None = None
    previous_snapshot_id: uuid.UUID | None = None

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0
