from __future__ import annotations

"""Integration tests for Phase 6C.0 historical price persistence against a
real PostgreSQL database.

Repository-level tests confirm natural-key idempotency (content hash for
PriceArchiveFile, (symbol, trading_date)/(benchmark_code, trading_date) for
DailyPrice/BenchmarkPrice). The end-to-end tests drive sync_daily_prices /
sync_benchmark_prices with fake in-memory providers (no live network) to
prove the full pipeline (fetch -> archive -> parse -> persist) works
against real repositories/constraints, including the holiday-skip and
anti-bot-block-stops-the-loop behaviors.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d
"""

from datetime import date
from decimal import Decimal

import pytest

from investing_agent.schemas.prices import (
    BenchmarkPriceCreate,
    DailyPriceCreate,
    PriceArchiveFileCreate,
)
from investing_agent.services.ingestion.common import ensure_company
from investing_agent.services.prices.interfaces import (
    ParsedBenchmarkPrice,
    ParsedDailyPrice,
    RawPriceFile,
)
from investing_agent.services.sources.interfaces import SourceAccessError

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session():
    import os

    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://investing:investing@localhost:5433/investing_agent_test"
    )
    from investing_agent.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


class _FakeHistoricalPriceProvider:
    """In-memory fetch_raw/parse — one Zip file per weekday, holidays return
    None. Bytes are just a marker string; parse() ignores raw.content and
    returns canned rows keyed by trading_date."""

    def __init__(self, rows_by_date: dict[date, list[ParsedDailyPrice]], holidays: set[date]) -> None:
        self._rows_by_date = rows_by_date
        self._holidays = holidays
        self.fetch_calls: list[date] = []

    async def fetch_raw(self, trading_date: date) -> RawPriceFile | None:
        self.fetch_calls.append(trading_date)
        if trading_date in self._holidays:
            return None
        return RawPriceFile(
            content=f"raw-{trading_date}".encode(),
            source_url=f"https://example.test/{trading_date}.zip",
            content_type="application/zip",
            fetched_at=None,  # not read by the ingestion service
        )

    def parse(self, raw: RawPriceFile, trading_date: date) -> list[ParsedDailyPrice]:
        return self._rows_by_date.get(trading_date, [])

    def source_format(self, trading_date: date) -> str:
        return "NSE_CM_UDIFF"


class _BlockedAfterFirstProvider:
    """Raises SourceAccessError on the second call — proves the sync loop
    stops immediately rather than continuing to hammer a block."""

    def __init__(self) -> None:
        self.calls = 0

    async def fetch_raw(self, trading_date: date) -> RawPriceFile | None:
        self.calls += 1
        if self.calls >= 2:
            raise SourceAccessError("blocked")
        return RawPriceFile(
            content=b"raw",
            source_url="https://example.test/blocked.zip",
            content_type="application/zip",
            fetched_at=None,
        )

    def parse(self, raw: RawPriceFile, trading_date: date) -> list[ParsedDailyPrice]:
        return []

    def source_format(self, trading_date: date) -> str:
        return "NSE_CM_UDIFF"


class _FakeBenchmarkPriceProvider:
    def __init__(self, rows_by_date: dict[date, list[ParsedBenchmarkPrice]], holidays: set[date]) -> None:
        self._rows_by_date = rows_by_date
        self._holidays = holidays

    async def fetch_raw(self, trading_date: date) -> RawPriceFile | None:
        if trading_date in self._holidays:
            return None
        return RawPriceFile(
            content=f"raw-{trading_date}".encode(),
            source_url=f"https://example.test/index_{trading_date}.csv",
            content_type="text/csv",
            fetched_at=None,
        )

    def parse(self, raw: RawPriceFile, trading_date: date) -> list[ParsedBenchmarkPrice]:
        return self._rows_by_date.get(trading_date, [])

    def source_format(self, trading_date: date) -> str:
        return "NSE_INDICES_CLOSE_ALL"


class TestPriceArchiveFileRepositoryIdempotency:
    async def test_get_or_create_is_idempotent_on_natural_key(self, db_session) -> None:
        from investing_agent.db.repositories.prices import PriceArchiveFileRepository

        repo = PriceArchiveFileRepository(db_session)
        data = PriceArchiveFileCreate(
            data_type="EQUITY_BHAVCOPY",
            source_format="NSE_CM_UDIFF",
            trading_date=date(2025, 1, 2),
            source_url="https://example.test/a.zip",
            content_hash="abc123",
            storage_path="NSE_EQUITY_BHAVCOPY/abc123.zip",
            size_bytes=100,
        )
        row1, created1 = await repo.get_or_create(data)
        row2, created2 = await repo.get_or_create(data)

        assert created1 is True
        assert created2 is False
        assert row1.id == row2.id


class TestDailyPriceRepositoryIdempotency:
    async def test_create_if_not_exists_is_idempotent_on_symbol_date(self, db_session) -> None:
        from investing_agent.db.repositories.prices import DailyPriceRepository

        company = await ensure_company(db_session, "PHASE6C0TESTA")
        repo = DailyPriceRepository(db_session)
        data = DailyPriceCreate(
            company_id=company.id,
            symbol="PHASE6C0TESTA",
            trading_date=date(2025, 1, 2),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=1000,
            source="NSE_BHAVCOPY",
        )
        row1, created1 = await repo.create_if_not_exists(data)
        row2, created2 = await repo.create_if_not_exists(data)

        assert created1 is True
        assert created2 is False
        assert row1.id == row2.id
        assert row1.adjustment_status == "RAW"


class TestBenchmarkPriceRepositoryIdempotency:
    async def test_create_if_not_exists_is_idempotent_on_code_date(self, db_session) -> None:
        from investing_agent.db.repositories.prices import BenchmarkPriceRepository

        repo = BenchmarkPriceRepository(db_session)
        data = BenchmarkPriceCreate(
            benchmark_code="PHASE6C0TESTBENCH",
            trading_date=date(2025, 1, 2),
            open=Decimal("23783"),
            high=Decimal("24226.7"),
            low=Decimal("23751.55"),
            close=Decimal("24188.65"),
            source="NSE_INDICES_CLOSE_ALL",
        )
        row1, created1 = await repo.create_if_not_exists(data)
        row2, created2 = await repo.create_if_not_exists(data)

        assert created1 is True
        assert created2 is False
        assert row1.id == row2.id


class TestSyncDailyPricesEndToEnd:
    async def test_full_pipeline_with_holiday_skip(self, db_session) -> None:
        from investing_agent.services.prices.ingestion import sync_daily_prices

        symbol = "PHASE6C0TESTB"
        d1, holiday, d2 = date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)
        rows_by_date = {
            d1: [ParsedDailyPrice(symbol, d1, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100.5"), 1000)],
            d2: [ParsedDailyPrice(symbol, d2, Decimal("101"), Decimal("102"), Decimal("100"), Decimal("101.5"), 1100)],
        }
        provider = _FakeHistoricalPriceProvider(rows_by_date, holidays={holiday})

        summary = await sync_daily_prices(db_session, symbol, d1, d2, provider)

        assert summary.dates_checked == 3  # d1, holiday, d2 (all weekdays)
        assert summary.dates_no_data == 1
        assert summary.rows_created == 2
        assert summary.rows_skipped_duplicate == 0

        # Re-running is fully idempotent.
        summary2 = await sync_daily_prices(db_session, symbol, d1, d2, provider)
        assert summary2.rows_created == 0
        assert summary2.rows_skipped_duplicate == 2

    async def test_blocked_source_stops_the_loop(self, db_session) -> None:
        from investing_agent.services.prices.ingestion import sync_daily_prices

        provider = _BlockedAfterFirstProvider()
        summary = await sync_daily_prices(
            db_session, "PHASE6C0TESTC", date(2025, 1, 2), date(2025, 1, 8), provider
        )
        assert provider.calls == 2  # stopped immediately after the block, not all 5 weekdays
        assert len(summary.errors) == 1
        assert "blocked" in summary.errors[0]


class _FakeMultiSymbolProvider:
    """One weekday -> rows for several symbols at once, mirroring a real
    bhavcopy file that spans the whole market in a single fetch."""

    def __init__(self, rows_by_date: dict[date, list[ParsedDailyPrice]]) -> None:
        self._rows_by_date = rows_by_date
        self.fetch_calls: list[date] = []

    async def fetch_raw(self, trading_date: date) -> RawPriceFile | None:
        self.fetch_calls.append(trading_date)
        if trading_date not in self._rows_by_date:
            return None
        return RawPriceFile(
            content=f"raw-{trading_date}".encode(),
            source_url=f"https://example.test/{trading_date}.zip",
            content_type="application/zip",
            fetched_at=None,
        )

    def parse(self, raw: RawPriceFile, trading_date: date) -> list[ParsedDailyPrice]:
        return self._rows_by_date.get(trading_date, [])

    def source_format(self, trading_date: date) -> str:
        return "NSE_CM_UDIFF"


class TestSyncDailyPricesMultiEndToEnd:
    async def test_one_fetch_per_day_persists_all_requested_symbols(self, db_session) -> None:
        from investing_agent.services.prices.ingestion import sync_daily_prices_multi

        d1 = date(2025, 1, 2)
        symbols = ["PHASE6C0TESTD", "PHASE6C0TESTE"]
        rows_by_date = {
            d1: [
                ParsedDailyPrice(symbols[0], d1, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100.5"), 1000),
                ParsedDailyPrice(symbols[1], d1, Decimal("200"), Decimal("201"), Decimal("199"), Decimal("200.5"), 2000),
                ParsedDailyPrice("SOMEOTHERSYMBOL", d1, Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"), 1),
            ],
        }
        provider = _FakeMultiSymbolProvider(rows_by_date)

        summary = await sync_daily_prices_multi(db_session, symbols, d1, d1, provider)

        assert provider.fetch_calls == [d1]  # exactly one fetch, not one per symbol
        assert summary.rows_created == 2  # the unrequested symbol's row is not persisted

        from investing_agent.db.repositories.prices import DailyPriceRepository

        for symbol in symbols:
            rows = await DailyPriceRepository(db_session).list_between(symbol, d1, d1)
            assert len(rows) == 1


class TestSyncBenchmarkPricesEndToEnd:
    async def test_full_pipeline(self, db_session) -> None:
        from investing_agent.services.prices.ingestion import sync_benchmark_prices

        code = "PHASE6C0TESTBENCH2"
        d1 = date(2025, 1, 2)
        rows_by_date = {
            d1: [ParsedBenchmarkPrice(code, d1, Decimal("23783"), Decimal("24226.7"), Decimal("23751.55"), Decimal("24188.65"))],
        }
        provider = _FakeBenchmarkPriceProvider(rows_by_date, holidays=set())

        summary = await sync_benchmark_prices(db_session, code, d1, d1, provider)

        assert summary.rows_created == 1

        from investing_agent.db.repositories.prices import BenchmarkPriceRepository

        rows = await BenchmarkPriceRepository(db_session).list_between(code, d1, d1)
        assert len(rows) == 1
        assert rows[0].close == Decimal("24188.65")
        assert rows[0].source_document_id is not None

    async def test_range_source_archives_once_and_excludes_non_trading_days(self, db_session) -> None:
        from investing_agent.services.prices.ingestion import sync_benchmark_prices

        class RangeProvider:
            async def fetch_range(self, start_date, end_date):
                return RawPriceFile(
                    content=b'{"official":"range-response"}',
                    source_url="https://example.test/tri?start=2025-01-02&end=2025-01-05",
                    content_type="application/json",
                    fetched_at=None,
                )

            def parse_range(self, raw):
                return [
                    ParsedBenchmarkPrice("PHASE6GTRI", date(2025, 1, 2), *(Decimal("100"),) * 4),
                    # Saturday values in a range response must not become benchmark observations.
                    ParsedBenchmarkPrice("PHASE6GTRI", date(2025, 1, 4), *(Decimal("101"),) * 4),
                ]

            def source_format(self, trading_date):
                return "NIFTY_INDICES_TOTAL_RETURN"

        summary = await sync_benchmark_prices(
            db_session, "PHASE6GTRI", date(2025, 1, 2), date(2025, 1, 5), RangeProvider()
        )
        assert summary.rows_created == 1
        assert summary.dates_checked == 2
        assert summary.dates_no_data == 1

        from investing_agent.db.repositories.prices import BenchmarkPriceRepository

        rows = await BenchmarkPriceRepository(db_session).list_between(
            "PHASE6GTRI", date(2025, 1, 2), date(2025, 1, 5)
        )
        assert [row.trading_date for row in rows] == [date(2025, 1, 2)]
