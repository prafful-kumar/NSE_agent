from __future__ import annotations

"""Provider-neutral domain schemas for historical broker-account import
(Phase 6A).

These model a broker ACCOUNT and its historical activity (trades, cash
flows, dividends) independently of which broker the data came from. A
concrete importer (e.g. ZerodhaStatementImporter) is responsible for
mapping a broker-specific export format onto these shapes; nothing here
should ever need to change to add a new broker.

Idempotency: every fact row (trade / cash flow / dividend) carries a
`dedupe_key` computed by the importer from a stable identifier in the
source data (e.g. Zerodha's own `trade_id` for trades). Statement exports
routinely overlap in date range (Zerodha Console tradebook exports are
capped at 365 days per download, so a multi-year history requires several
overlapping files) — re-importing an overlapping file must not duplicate
rows, so the (broker_account_id, dedupe_key) pair is the only uniqueness
guarantee, never file identity or row order.

HistoricalPositionLot is the OUTPUT of position reconstruction (Phase 6B),
not something an importer writes directly — it is declared here now so the
importer's dedupe keys and the reconstruction logic share one vocabulary
from the start, per the user's explicit request to define all Phase 6A/6B
domain models up front.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from investing_agent.schemas.common import BaseSchema, TimestampedSchema

Broker = Literal["ZERODHA", "INDMONEY"]
StrategyProfile = Literal["LONG_TERM", "MEDIUM_TERM"]

# Fixed mapping requested by the user: which broker feeds which strategy
# bucket. Not user-configurable per account -- a second account on the same
# broker is still the same strategy profile.
BROKER_STRATEGY_PROFILE: dict[Broker, StrategyProfile] = {
    "ZERODHA": "LONG_TERM",
    "INDMONEY": "MEDIUM_TERM",
}

TradeSide = Literal["BUY", "SELL"]
CashFlowType = Literal["DEPOSIT", "WITHDRAWAL", "CHARGE", "OTHER"]
ImportRunStatus = Literal["SUCCESS", "PARTIAL", "FAILED"]


class BrokerAccountCreate(BaseSchema):
    user_id: str
    broker: Broker
    account_label: str  # e.g. "zerodha-primary" -- distinguishes multiple accounts at one broker
    strategy_profile: StrategyProfile | None = None  # None -> resolved from BROKER_STRATEGY_PROFILE


class BrokerAccountRead(TimestampedSchema):
    id: uuid.UUID
    user_id: str
    broker: Broker
    account_label: str
    strategy_profile: StrategyProfile


class HistoricalTradeCreate(BaseSchema):
    broker_account_id: uuid.UUID
    dedupe_key: str
    symbol: str
    isin: str | None = None
    company_id: uuid.UUID | None = None
    trade_date: date
    trade_datetime: datetime | None = None
    side: TradeSide
    quantity: Decimal
    price: Decimal
    exchange: str | None = None
    segment: str | None = None
    product: str | None = None  # CNC | MIS | etc.
    order_id: str | None = None
    charges: Decimal | None = None
    source_import_run_id: uuid.UUID | None = None
    raw: dict[str, Any] | None = None  # original row, for audit/debugging


class HistoricalTradeRead(TimestampedSchema):
    id: uuid.UUID
    broker_account_id: uuid.UUID
    symbol: str
    isin: str | None
    company_id: uuid.UUID | None
    trade_date: date
    trade_datetime: datetime | None
    side: TradeSide
    quantity: Decimal
    price: Decimal
    exchange: str | None
    segment: str | None
    product: str | None
    order_id: str | None
    charges: Decimal | None


class HistoricalCashFlowCreate(BaseSchema):
    broker_account_id: uuid.UUID
    dedupe_key: str
    flow_date: date
    flow_type: CashFlowType
    amount: Decimal  # positive for deposits/credits, negative for withdrawals/debits
    description: str | None = None
    source_import_run_id: uuid.UUID | None = None
    raw: dict[str, Any] | None = None


class HistoricalCashFlowRead(TimestampedSchema):
    id: uuid.UUID
    broker_account_id: uuid.UUID
    flow_date: date
    flow_type: CashFlowType
    amount: Decimal
    description: str | None


class HistoricalDividendCreate(BaseSchema):
    broker_account_id: uuid.UUID
    dedupe_key: str
    symbol: str
    isin: str | None = None
    company_id: uuid.UUID | None = None
    payment_date: date
    quantity_held: Decimal | None = None
    amount: Decimal
    source_import_run_id: uuid.UUID | None = None
    raw: dict[str, Any] | None = None


class HistoricalDividendRead(TimestampedSchema):
    id: uuid.UUID
    broker_account_id: uuid.UUID
    symbol: str
    company_id: uuid.UUID | None
    payment_date: date
    quantity_held: Decimal | None
    amount: Decimal


class HistoricalPositionLotCreate(BaseSchema):
    """Output of Phase 6B lot reconstruction (FIFO) -- not written by
    importers. Declared now so the eventual reconstruction service has a
    settled target shape."""

    broker_account_id: uuid.UUID
    symbol: str
    company_id: uuid.UUID | None = None
    opened_date: date
    quantity_opened: Decimal
    quantity_remaining: Decimal
    cost_price: Decimal
    closed_date: date | None = None
    realized_pnl: Decimal | None = None
    opening_trade_id: uuid.UUID | None = None


class HistoricalPositionLotRead(TimestampedSchema):
    id: uuid.UUID
    broker_account_id: uuid.UUID
    symbol: str
    company_id: uuid.UUID | None
    opened_date: date
    quantity_opened: Decimal
    quantity_remaining: Decimal
    cost_price: Decimal
    closed_date: date | None
    realized_pnl: Decimal | None


class HistoricalImportRunCreate(BaseSchema):
    broker_account_id: uuid.UUID
    source_type: str  # e.g. "zerodha_tradebook_csv", "zerodha_funds_statement_csv"
    file_name: str
    file_hash: str  # sha256 of file bytes -- run-level idempotency
    status: ImportRunStatus
    trades_created: int = 0
    trades_skipped_duplicate: int = 0
    cash_flows_created: int = 0
    cash_flows_skipped_duplicate: int = 0
    dividends_created: int = 0
    dividends_skipped_duplicate: int = 0
    date_range_start: date | None = None
    date_range_end: date | None = None
    warnings: list[str] | None = None
    error_message: str | None = None


class HistoricalImportRunRead(TimestampedSchema):
    id: uuid.UUID
    broker_account_id: uuid.UUID
    source_type: str
    file_name: str
    file_hash: str
    status: ImportRunStatus
    trades_created: int
    trades_skipped_duplicate: int
    cash_flows_created: int
    cash_flows_skipped_duplicate: int
    dividends_created: int
    dividends_skipped_duplicate: int
    date_range_start: date | None
    date_range_end: date | None
    warnings: list[str] | None
    error_message: str | None
