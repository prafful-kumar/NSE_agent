from __future__ import annotations

"""Repositories for historical market prices (Phase 6C.0).

PriceArchiveFile/DailyPrice/BenchmarkPrice are all immutable facts, same
discipline as Phase 6A's HistoricalTrade -- a re-sync of an unchanged
source is a no-op (idempotent on the natural key), never a duplicate row
or a field-by-field update.
"""

from datetime import date, timedelta

from sqlalchemy import func, select

from investing_agent.db.models import BenchmarkPrice, DailyPrice, PriceArchiveFile
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.prices import (
    BenchmarkPriceCreate,
    DailyPriceCreate,
    PriceArchiveFileCreate,
)


class PriceArchiveFileRepository(BaseRepository[PriceArchiveFile]):
    model = PriceArchiveFile

    async def get_by_hash(
        self, trading_date: date, data_type: str, content_hash: str
    ) -> PriceArchiveFile | None:
        result = await self.session.execute(
            select(PriceArchiveFile).where(
                PriceArchiveFile.trading_date == trading_date,
                PriceArchiveFile.data_type == data_type,
                PriceArchiveFile.content_hash == content_hash,
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, data: PriceArchiveFileCreate) -> tuple[PriceArchiveFile, bool]:
        existing = await self.get_by_hash(data.trading_date, data.data_type, data.content_hash)
        if existing is not None:
            return existing, False
        row = PriceArchiveFile(**data.model_dump())
        return await self.add(row), True


class DailyPriceRepository(BaseRepository[DailyPrice]):
    model = DailyPrice

    async def get_by_symbol_date(self, symbol: str, trading_date: date) -> DailyPrice | None:
        result = await self.session.execute(
            select(DailyPrice).where(
                DailyPrice.symbol == symbol,
                DailyPrice.trading_date == trading_date,
            )
        )
        return result.scalar_one_or_none()

    async def create_if_not_exists(self, data: DailyPriceCreate) -> tuple[DailyPrice, bool]:
        existing = await self.get_by_symbol_date(data.symbol, data.trading_date)
        if existing is not None:
            return existing, False
        row = DailyPrice(**data.model_dump())
        return await self.add(row), True

    async def list_between(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[DailyPrice]:
        result = await self.session.execute(
            select(DailyPrice)
            .where(
                DailyPrice.symbol == symbol,
                DailyPrice.trading_date >= start_date,
                DailyPrice.trading_date <= end_date,
            )
            .order_by(DailyPrice.trading_date)
        )
        return list(result.scalars().all())

    async def get_nearest_on_or_before(self, symbol: str, target_date: date) -> DailyPrice | None:
        """Latest EQ-series bar with trading_date <= target_date. Unbounded
        lookback (an entry price is expected close to the target, and a
        missing recent bar should still resolve to whatever real data
        exists rather than an arbitrary window)."""
        result = await self.session.execute(
            select(DailyPrice)
            .where(DailyPrice.symbol == symbol, DailyPrice.trading_date <= target_date)
            .order_by(DailyPrice.trading_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_nearest_on_or_after(
        self, symbol: str, target_date: date, max_lookahead_days: int = 10
    ) -> DailyPrice | None:
        """Earliest EQ-series bar with trading_date >= target_date, bounded
        to max_lookahead_days so a genuine multi-day data gap surfaces as
        "not found" rather than scanning indefinitely into the future."""
        result = await self.session.execute(
            select(DailyPrice)
            .where(
                DailyPrice.symbol == symbol,
                DailyPrice.trading_date >= target_date,
                DailyPrice.trading_date <= target_date + timedelta(days=max_lookahead_days),
            )
            .order_by(DailyPrice.trading_date.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_trading_date(self, symbol: str) -> date | None:
        """The most recent trading_date this symbol has any bar for --
        used to distinguish "target date is beyond all available data"
        (INSUFFICIENT_HORIZON) from "a mid-series gap around the target
        date" (DATA_GAP) when get_nearest_on_or_after finds nothing."""
        result = await self.session.execute(
            select(func.max(DailyPrice.trading_date)).where(DailyPrice.symbol == symbol)
        )
        return result.scalar_one_or_none()


class BenchmarkPriceRepository(BaseRepository[BenchmarkPrice]):
    model = BenchmarkPrice

    async def get_by_code_date(
        self, benchmark_code: str, trading_date: date
    ) -> BenchmarkPrice | None:
        result = await self.session.execute(
            select(BenchmarkPrice).where(
                BenchmarkPrice.benchmark_code == benchmark_code,
                BenchmarkPrice.trading_date == trading_date,
            )
        )
        return result.scalar_one_or_none()

    async def create_if_not_exists(
        self, data: BenchmarkPriceCreate
    ) -> tuple[BenchmarkPrice, bool]:
        existing = await self.get_by_code_date(data.benchmark_code, data.trading_date)
        if existing is not None:
            return existing, False
        row = BenchmarkPrice(**data.model_dump())
        return await self.add(row), True

    async def list_between(
        self, benchmark_code: str, start_date: date, end_date: date
    ) -> list[BenchmarkPrice]:
        result = await self.session.execute(
            select(BenchmarkPrice)
            .where(
                BenchmarkPrice.benchmark_code == benchmark_code,
                BenchmarkPrice.trading_date >= start_date,
                BenchmarkPrice.trading_date <= end_date,
            )
            .order_by(BenchmarkPrice.trading_date)
        )
        return list(result.scalars().all())

    async def get_nearest_on_or_before(
        self, benchmark_code: str, target_date: date
    ) -> BenchmarkPrice | None:
        result = await self.session.execute(
            select(BenchmarkPrice)
            .where(
                BenchmarkPrice.benchmark_code == benchmark_code,
                BenchmarkPrice.trading_date <= target_date,
            )
            .order_by(BenchmarkPrice.trading_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_nearest_on_or_after(
        self, benchmark_code: str, target_date: date, max_lookahead_days: int = 10
    ) -> BenchmarkPrice | None:
        result = await self.session.execute(
            select(BenchmarkPrice)
            .where(
                BenchmarkPrice.benchmark_code == benchmark_code,
                BenchmarkPrice.trading_date >= target_date,
                BenchmarkPrice.trading_date <= target_date + timedelta(days=max_lookahead_days),
            )
            .order_by(BenchmarkPrice.trading_date.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_trading_date(self, benchmark_code: str) -> date | None:
        result = await self.session.execute(
            select(func.max(BenchmarkPrice.trading_date)).where(
                BenchmarkPrice.benchmark_code == benchmark_code
            )
        )
        return result.scalar_one_or_none()
