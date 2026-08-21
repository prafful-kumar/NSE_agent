from __future__ import annotations

"""Repositories for broker-account historical import (Phase 6A).

HistoricalTrade/CashFlow/Dividend are immutable facts identified by
(broker_account_id, dedupe_key) -- unlike FinancialResult there is no
versioning/correction concept here: a broker's own trade_id (or an
importer-computed stable key) never legitimately changes value, so a
dedupe_key match is always treated as "already imported, skip" rather than
compared field-by-field for a possible update.
"""

import uuid
from datetime import date

from sqlalchemy import delete, select

from investing_agent.db.models import (
    BrokerAccount,
    HistoricalCashFlow,
    HistoricalDividend,
    HistoricalImportRun,
    HistoricalPositionLot,
    HistoricalTrade,
    OpeningPositionAdjustment,
)
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.broker_history import (
    BROKER_STRATEGY_PROFILE,
    BrokerAccountCreate,
    HistoricalCashFlowCreate,
    HistoricalDividendCreate,
    HistoricalImportRunCreate,
    HistoricalPositionLotCreate,
    HistoricalTradeCreate,
)
from investing_agent.schemas.corporate_actions import OpeningPositionAdjustmentCreate


class BrokerAccountRepository(BaseRepository[BrokerAccount]):
    model = BrokerAccount

    async def get_by_label(self, user_id: str, broker: str, account_label: str) -> BrokerAccount | None:
        result = await self.session.execute(
            select(BrokerAccount).where(
                BrokerAccount.user_id == user_id,
                BrokerAccount.broker == broker,
                BrokerAccount.account_label == account_label,
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, data: BrokerAccountCreate) -> tuple[BrokerAccount, bool]:
        existing = await self.get_by_label(data.user_id, data.broker, data.account_label)
        if existing is not None:
            return existing, False
        strategy_profile = data.strategy_profile or BROKER_STRATEGY_PROFILE[data.broker]
        account = BrokerAccount(
            user_id=data.user_id,
            broker=data.broker,
            account_label=data.account_label,
            strategy_profile=strategy_profile,
        )
        return await self.add(account), True


class HistoricalImportRunRepository(BaseRepository[HistoricalImportRun]):
    model = HistoricalImportRun

    async def get_by_file_hash(self, broker_account_id: uuid.UUID, file_hash: str) -> HistoricalImportRun | None:
        result = await self.session.execute(
            select(HistoricalImportRun).where(
                HistoricalImportRun.broker_account_id == broker_account_id,
                HistoricalImportRun.file_hash == file_hash,
            )
        )
        return result.scalar_one_or_none()

    async def create(self, data: HistoricalImportRunCreate) -> HistoricalImportRun:
        row = HistoricalImportRun(**data.model_dump())
        return await self.add(row)


class HistoricalTradeRepository(BaseRepository[HistoricalTrade]):
    model = HistoricalTrade

    async def get_by_dedupe_key(self, broker_account_id: uuid.UUID, dedupe_key: str) -> HistoricalTrade | None:
        result = await self.session.execute(
            select(HistoricalTrade).where(
                HistoricalTrade.broker_account_id == broker_account_id,
                HistoricalTrade.dedupe_key == dedupe_key,
            )
        )
        return result.scalar_one_or_none()

    async def create_if_not_exists(self, data: HistoricalTradeCreate) -> tuple[HistoricalTrade, bool]:
        existing = await self.get_by_dedupe_key(data.broker_account_id, data.dedupe_key)
        if existing is not None:
            return existing, False
        row = HistoricalTrade(**data.model_dump())
        return await self.add(row), True

    async def list_up_to(self, broker_account_id: uuid.UUID, as_of_date: date) -> list[HistoricalTrade]:
        """All trades with trade_date <= as_of_date, ordered for deterministic
        replay (final tie-break happens in replay.py; this ordering just
        keeps the query itself deterministic)."""
        result = await self.session.execute(
            select(HistoricalTrade)
            .where(
                HistoricalTrade.broker_account_id == broker_account_id,
                HistoricalTrade.trade_date <= as_of_date,
            )
            .order_by(HistoricalTrade.trade_date, HistoricalTrade.dedupe_key)
        )
        return list(result.scalars().all())

    async def earliest_date(self, broker_account_id: uuid.UUID) -> date | None:
        from sqlalchemy import func

        result = await self.session.execute(
            select(func.min(HistoricalTrade.trade_date)).where(
                HistoricalTrade.broker_account_id == broker_account_id
            )
        )
        return result.scalar_one_or_none()

    async def latest_date(self, broker_account_id: uuid.UUID) -> date | None:
        from sqlalchemy import func

        result = await self.session.execute(
            select(func.max(HistoricalTrade.trade_date)).where(
                HistoricalTrade.broker_account_id == broker_account_id
            )
        )
        return result.scalar_one_or_none()

    async def list_distinct_symbol_dates(self, broker_account_id: uuid.UUID) -> list[tuple[str, date]]:
        """Every (symbol, trade_date) pair this account has a real trade on --
        the raw candidate set of genuine historical decision points for Phase
        6D's bulk walk-forward (before any freezing/scoring/exclusion)."""
        result = await self.session.execute(
            select(HistoricalTrade.symbol, HistoricalTrade.trade_date)
            .where(HistoricalTrade.broker_account_id == broker_account_id)
            .distinct()
            .order_by(HistoricalTrade.symbol, HistoricalTrade.trade_date)
        )
        return [(row.symbol, row.trade_date) for row in result.all()]

    async def earliest_date_for_symbol(
        self, broker_account_id: uuid.UUID, symbol: str, as_of_date: date
    ) -> date | None:
        """First trade date for this symbol at or before as_of_date -- used
        only for descriptive holding-age reporting, never for a decision
        rule; PIT-safe by construction since it's bounded by as_of_date."""
        from sqlalchemy import func

        result = await self.session.execute(
            select(func.min(HistoricalTrade.trade_date)).where(
                HistoricalTrade.broker_account_id == broker_account_id,
                HistoricalTrade.symbol == symbol,
                HistoricalTrade.trade_date <= as_of_date,
            )
        )
        return result.scalar_one_or_none()


class HistoricalCashFlowRepository(BaseRepository[HistoricalCashFlow]):
    model = HistoricalCashFlow

    async def get_by_dedupe_key(self, broker_account_id: uuid.UUID, dedupe_key: str) -> HistoricalCashFlow | None:
        result = await self.session.execute(
            select(HistoricalCashFlow).where(
                HistoricalCashFlow.broker_account_id == broker_account_id,
                HistoricalCashFlow.dedupe_key == dedupe_key,
            )
        )
        return result.scalar_one_or_none()

    async def create_if_not_exists(self, data: HistoricalCashFlowCreate) -> tuple[HistoricalCashFlow, bool]:
        existing = await self.get_by_dedupe_key(data.broker_account_id, data.dedupe_key)
        if existing is not None:
            return existing, False
        row = HistoricalCashFlow(**data.model_dump())
        return await self.add(row), True

    async def list_up_to(self, broker_account_id: uuid.UUID, as_of_date: date) -> list[HistoricalCashFlow]:
        result = await self.session.execute(
            select(HistoricalCashFlow).where(
                HistoricalCashFlow.broker_account_id == broker_account_id,
                HistoricalCashFlow.flow_date <= as_of_date,
            )
        )
        return list(result.scalars().all())

    async def earliest_date(self, broker_account_id: uuid.UUID) -> date | None:
        from sqlalchemy import func

        result = await self.session.execute(
            select(func.min(HistoricalCashFlow.flow_date)).where(
                HistoricalCashFlow.broker_account_id == broker_account_id
            )
        )
        return result.scalar_one_or_none()


class HistoricalDividendRepository(BaseRepository[HistoricalDividend]):
    model = HistoricalDividend

    async def get_by_dedupe_key(self, broker_account_id: uuid.UUID, dedupe_key: str) -> HistoricalDividend | None:
        result = await self.session.execute(
            select(HistoricalDividend).where(
                HistoricalDividend.broker_account_id == broker_account_id,
                HistoricalDividend.dedupe_key == dedupe_key,
            )
        )
        return result.scalar_one_or_none()

    async def create_if_not_exists(self, data: HistoricalDividendCreate) -> tuple[HistoricalDividend, bool]:
        existing = await self.get_by_dedupe_key(data.broker_account_id, data.dedupe_key)
        if existing is not None:
            return existing, False
        row = HistoricalDividend(**data.model_dump())
        return await self.add(row), True


class HistoricalPositionLotRepository(BaseRepository[HistoricalPositionLot]):
    """historical_position_lots is a materialized cache of the latest
    full-history FIFO reconstruction (see HistoricalPositionLot's own
    docstring) -- rebuilt wholesale, never incrementally patched, since one
    new historical trade can change every downstream lot's cost basis."""

    model = HistoricalPositionLot

    async def replace_all_for_account(
        self, broker_account_id: uuid.UUID, lots: list[HistoricalPositionLotCreate]
    ) -> list[HistoricalPositionLot]:
        await self.session.execute(
            delete(HistoricalPositionLot).where(
                HistoricalPositionLot.broker_account_id == broker_account_id
            )
        )
        rows = [HistoricalPositionLot(**lot.model_dump()) for lot in lots]
        self.session.add_all(rows)
        await self.session.flush()
        return rows

    async def list_for_account(self, broker_account_id: uuid.UUID) -> list[HistoricalPositionLot]:
        result = await self.session.execute(
            select(HistoricalPositionLot).where(
                HistoricalPositionLot.broker_account_id == broker_account_id
            )
        )
        return list(result.scalars().all())


class OpeningPositionAdjustmentRepository(BaseRepository[OpeningPositionAdjustment]):
    """Account-scoped, reconciliation-derived synthetic opening lots (Phase
    6B) -- see OpeningPositionAdjustment's own docstring. At most one row
    per (broker_account_id, symbol): a second `record` call for the same
    symbol replaces the estimate rather than stacking a duplicate lot."""

    model = OpeningPositionAdjustment

    async def get_by_symbol(
        self, broker_account_id: uuid.UUID, symbol: str
    ) -> OpeningPositionAdjustment | None:
        result = await self.session.execute(
            select(OpeningPositionAdjustment).where(
                OpeningPositionAdjustment.broker_account_id == broker_account_id,
                OpeningPositionAdjustment.symbol == symbol,
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self, data: OpeningPositionAdjustmentCreate
    ) -> tuple[OpeningPositionAdjustment, bool]:
        existing = await self.get_by_symbol(data.broker_account_id, data.symbol)
        if existing is not None:
            for field_name, value in data.model_dump().items():
                setattr(existing, field_name, value)
            await self.session.flush()
            return existing, False
        row = OpeningPositionAdjustment(**data.model_dump())
        return await self.add(row), True

    async def list_for_account(
        self, broker_account_id: uuid.UUID
    ) -> list[OpeningPositionAdjustment]:
        result = await self.session.execute(
            select(OpeningPositionAdjustment).where(
                OpeningPositionAdjustment.broker_account_id == broker_account_id
            )
        )
        return list(result.scalars().all())
