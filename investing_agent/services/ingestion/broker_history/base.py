from __future__ import annotations

"""Generic statement-importer interface (Phase 6A).

A StatementImporter turns one broker-specific export file into a
ParsedStatement -- plain, DB-agnostic dataclasses. It never touches the
database itself: symbol -> company_id resolution, dedupe-key upserts, and
HistoricalImportRun bookkeeping all happen in import_service.py, which is
shared across every broker. Adding a new broker means writing one new
StatementImporter subclass; nothing else in the pipeline changes.

dedupe_key is computed by the importer from whatever stable identifier the
source format provides (e.g. Zerodha tradebook's own `trade_id` column for
trades). It is the only thing that makes re-importing an overlapping-date
export idempotent -- see schemas/broker_history.py for the full rationale.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

from investing_agent.schemas.broker_history import Broker, CashFlowType, TradeSide


@dataclass(frozen=True)
class ParsedTrade:
    dedupe_key: str
    symbol: str
    trade_date: date
    side: TradeSide
    quantity: Decimal
    price: Decimal
    isin: str | None = None
    trade_datetime: datetime | None = None
    exchange: str | None = None
    segment: str | None = None
    product: str | None = None
    order_id: str | None = None
    charges: Decimal | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class ParsedCashFlow:
    dedupe_key: str
    flow_date: date
    flow_type: CashFlowType
    amount: Decimal
    description: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class ParsedDividend:
    dedupe_key: str
    symbol: str
    payment_date: date
    amount: Decimal
    isin: str | None = None
    quantity_held: Decimal | None = None
    raw: dict[str, Any] | None = None


@dataclass
class ParsedStatement:
    """Result of parsing one statement file. source_type identifies which
    kind of export this was (e.g. "zerodha_tradebook_csv") -- distinct
    exports from the same broker (tradebook vs funds statement) are
    parsed by different calls, not merged into one file."""

    source_type: str
    trades: list[ParsedTrade] = field(default_factory=list)
    cash_flows: list[ParsedCashFlow] = field(default_factory=list)
    dividends: list[ParsedDividend] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    date_range_start: date | None = None
    date_range_end: date | None = None


class StatementImporter(ABC):
    """One subclass per broker export family."""

    broker: ClassVar[Broker]

    @abstractmethod
    def parse(self, file_path: Path) -> ParsedStatement:
        """Parse a single exported statement file. Must not perform any
        I/O beyond reading file_path, and must not touch the database."""
        ...


_REGISTRY: dict[Broker, type[StatementImporter]] = {}


def register_importer(importer_cls: type[StatementImporter]) -> type[StatementImporter]:
    _REGISTRY[importer_cls.broker] = importer_cls
    return importer_cls


def get_importer(broker: Broker) -> StatementImporter:
    importer_cls = _REGISTRY.get(broker)
    if importer_cls is None:
        raise KeyError(f"No StatementImporter registered for broker={broker!r}")
    return importer_cls()
