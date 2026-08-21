from __future__ import annotations

"""Historical market price schemas (Phase 6C.0).

DailyPrice/BenchmarkPrice are deterministically parsed from archived NSE
bhavcopy / index close-all files -- never LLM-parsed, never adjusted for
corporate actions. adjustment_status is always "RAW" today; see the
PriceArchiveFile/DailyPrice docstrings in db/models.py for why raw prices
are a deliberate choice, not a stopgap.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from investing_agent.schemas.common import BaseSchema, TimestampedSchema

PriceDataType = Literal["EQUITY_BHAVCOPY", "INDEX_BHAVCOPY", "INDEX_TOTAL_RETURN"]
PriceSourceFormat = Literal[
    "NSE_CM_UDIFF",
    "NSE_CM_LEGACY",
    "NSE_INDICES_CLOSE_ALL",
    "NIFTY_INDICES_TOTAL_RETURN",
]
AdjustmentStatus = Literal["RAW"]


class PriceArchiveFileCreate(BaseSchema):
    data_type: PriceDataType
    source_format: PriceSourceFormat
    trading_date: date
    source_url: str
    content_hash: str
    storage_path: str
    mime_type: str | None = None
    size_bytes: int | None = None


class PriceArchiveFileRead(TimestampedSchema):
    id: uuid.UUID
    data_type: PriceDataType
    source_format: PriceSourceFormat
    trading_date: date
    source_url: str
    content_hash: str
    storage_path: str
    mime_type: str | None = None
    size_bytes: int | None = None
    fetched_at: datetime


class DailyPriceCreate(BaseSchema):
    company_id: uuid.UUID
    symbol: str
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None = None
    source: str
    source_document_id: uuid.UUID | None = None
    adjustment_status: AdjustmentStatus = "RAW"


class DailyPriceRead(TimestampedSchema):
    id: uuid.UUID
    company_id: uuid.UUID
    symbol: str
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None = None
    source: str
    source_document_id: uuid.UUID | None = None
    adjustment_status: AdjustmentStatus
    ingested_at: datetime


class BenchmarkPriceCreate(BaseSchema):
    benchmark_code: str
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    source: str
    source_document_id: uuid.UUID | None = None


class BenchmarkPriceRead(TimestampedSchema):
    id: uuid.UUID
    benchmark_code: str
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    source: str
    source_document_id: uuid.UUID | None = None
    ingested_at: datetime
