from __future__ import annotations

"""Historical price sync orchestration (Phase 6C.0).

Pipeline per trading date, mirroring services/ingestion/common.py's
discover -> archive -> parse -> persist order: fetch_raw -> archive the
whole file once (PriceArchiveFile, content-hash idempotent) -> parse
(deterministic, whole file) -> filter to the requested symbol/benchmark ->
persist (DailyPrice/BenchmarkPrice, natural-key idempotent).

The parser always parses every row in the file (cheap, deterministic) even
though only one symbol is persisted per sync call today -- this keeps the
parser itself genuinely generic rather than symbol-aware, so a later
"sync the whole market" pipeline is a persistence-layer change only, not a
parser rewrite.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.repositories.prices import (
    BenchmarkPriceRepository,
    DailyPriceRepository,
    PriceArchiveFileRepository,
)
from investing_agent.schemas.prices import (
    BenchmarkPriceCreate,
    DailyPriceCreate,
    PriceArchiveFileCreate,
)
from investing_agent.services.ingestion.common import ensure_company
from investing_agent.services.prices.interfaces import BenchmarkPriceProvider, HistoricalPriceProvider
from investing_agent.services.sources.interfaces import SourceAccessError, SourceTransientError
from investing_agent.services.storage import write_archive

log = structlog.get_logger(__name__)


@dataclass
class PriceSyncSummary:
    dates_checked: int = 0
    dates_no_data: int = 0  # weekend/holiday
    rows_created: int = 0
    rows_skipped_duplicate: int = 0
    errors: list[str] = field(default_factory=list)


def _weekdays_between(start_date: date, end_date: date) -> list[date]:
    out = []
    d = start_date
    while d <= end_date:
        if d.weekday() < 5:  # Mon-Fri; actual holidays still come back as 404/None
            out.append(d)
        d += timedelta(days=1)
    return out


async def sync_daily_prices(
    session: AsyncSession,
    symbol: str,
    start_date: date,
    end_date: date,
    provider: HistoricalPriceProvider,
) -> PriceSyncSummary:
    summary = PriceSyncSummary()
    company = await ensure_company(session, symbol)
    archive_repo = PriceArchiveFileRepository(session)
    price_repo = DailyPriceRepository(session)

    for trading_date in _weekdays_between(start_date, end_date):
        summary.dates_checked += 1
        try:
            raw = await provider.fetch_raw(trading_date)
        except SourceAccessError as exc:
            log.warning("daily_price_sync.blocked", symbol=symbol, trading_date=str(trading_date))
            summary.errors.append(f"{trading_date}: blocked — {exc}")
            break  # do not keep hammering an anti-bot block
        except SourceTransientError as exc:
            log.warning(
                "daily_price_sync.transient_error", symbol=symbol, trading_date=str(trading_date)
            )
            summary.errors.append(f"{trading_date}: {exc}")
            continue

        if raw is None:
            summary.dates_no_data += 1
            continue

        content_hash = hashlib.sha256(raw.content).hexdigest()
        storage_path = write_archive(raw.content, "NSE_EQUITY_BHAVCOPY", content_hash, "zip")
        archive_file, _ = await archive_repo.get_or_create(
            PriceArchiveFileCreate(
                data_type="EQUITY_BHAVCOPY",
                source_format=provider.source_format(trading_date),
                trading_date=trading_date,
                source_url=raw.source_url,
                content_hash=content_hash,
                storage_path=storage_path,
                mime_type=raw.content_type,
                size_bytes=len(raw.content),
            )
        )

        rows = provider.parse(raw, trading_date)
        for row in rows:
            if row.symbol != symbol.upper():
                continue
            _, was_created = await price_repo.create_if_not_exists(
                DailyPriceCreate(
                    company_id=company.id,
                    symbol=row.symbol,
                    trading_date=row.trading_date,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    source="NSE_BHAVCOPY",
                    source_document_id=archive_file.id,
                    adjustment_status="RAW",
                )
            )
            if was_created:
                summary.rows_created += 1
            else:
                summary.rows_skipped_duplicate += 1

    return summary


async def sync_daily_prices_multi(
    session: AsyncSession,
    symbols: list[str],
    start_date: date,
    end_date: date,
    provider: HistoricalPriceProvider,
) -> PriceSyncSummary:
    """Same fetch -> archive -> parse -> persist pipeline as
    sync_daily_prices, but persists matching rows for every symbol in
    `symbols` from a single per-day fetch instead of re-fetching the same
    day's bhavcopy once per symbol. Needed for bulk-backfilling many symbols
    over years of history without redundantly refetching the same file N
    times per day (N = number of symbols)."""
    summary = PriceSyncSummary()
    wanted = {s.upper() for s in symbols}
    companies = {s: await ensure_company(session, s) for s in wanted}
    archive_repo = PriceArchiveFileRepository(session)
    price_repo = DailyPriceRepository(session)

    for trading_date in _weekdays_between(start_date, end_date):
        summary.dates_checked += 1
        try:
            raw = await provider.fetch_raw(trading_date)
        except SourceAccessError as exc:
            log.warning("daily_price_sync_multi.blocked", trading_date=str(trading_date))
            summary.errors.append(f"{trading_date}: blocked — {exc}")
            break  # do not keep hammering an anti-bot block
        except SourceTransientError as exc:
            log.warning("daily_price_sync_multi.transient_error", trading_date=str(trading_date))
            summary.errors.append(f"{trading_date}: {exc}")
            continue

        if raw is None:
            summary.dates_no_data += 1
            continue

        content_hash = hashlib.sha256(raw.content).hexdigest()
        storage_path = write_archive(raw.content, "NSE_EQUITY_BHAVCOPY", content_hash, "zip")
        archive_file, _ = await archive_repo.get_or_create(
            PriceArchiveFileCreate(
                data_type="EQUITY_BHAVCOPY",
                source_format=provider.source_format(trading_date),
                trading_date=trading_date,
                source_url=raw.source_url,
                content_hash=content_hash,
                storage_path=storage_path,
                mime_type=raw.content_type,
                size_bytes=len(raw.content),
            )
        )

        rows = provider.parse(raw, trading_date)
        for row in rows:
            if row.symbol not in wanted:
                continue
            _, was_created = await price_repo.create_if_not_exists(
                DailyPriceCreate(
                    company_id=companies[row.symbol].id,
                    symbol=row.symbol,
                    trading_date=row.trading_date,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    source="NSE_BHAVCOPY",
                    source_document_id=archive_file.id,
                    adjustment_status="RAW",
                )
            )
            if was_created:
                summary.rows_created += 1
            else:
                summary.rows_skipped_duplicate += 1

    return summary


async def sync_benchmark_prices(
    session: AsyncSession,
    benchmark_code: str,
    start_date: date,
    end_date: date,
    provider: BenchmarkPriceProvider,
) -> PriceSyncSummary:
    summary = PriceSyncSummary()
    archive_repo = PriceArchiveFileRepository(session)
    price_repo = BenchmarkPriceRepository(session)

    for trading_date in _weekdays_between(start_date, end_date):
        summary.dates_checked += 1
        try:
            raw = await provider.fetch_raw(trading_date)
        except SourceAccessError as exc:
            log.warning(
                "benchmark_price_sync.blocked", benchmark_code=benchmark_code,
                trading_date=str(trading_date),
            )
            summary.errors.append(f"{trading_date}: blocked — {exc}")
            break
        except SourceTransientError as exc:
            log.warning(
                "benchmark_price_sync.transient_error", benchmark_code=benchmark_code,
                trading_date=str(trading_date),
            )
            summary.errors.append(f"{trading_date}: {exc}")
            continue

        if raw is None:
            summary.dates_no_data += 1
            continue

        content_hash = hashlib.sha256(raw.content).hexdigest()
        storage_path = write_archive(raw.content, "NSE_INDEX_BHAVCOPY", content_hash, "csv")
        archive_file, _ = await archive_repo.get_or_create(
            PriceArchiveFileCreate(
                data_type="INDEX_BHAVCOPY",
                source_format=provider.source_format(trading_date),
                trading_date=trading_date,
                source_url=raw.source_url,
                content_hash=content_hash,
                storage_path=storage_path,
                mime_type=raw.content_type,
                size_bytes=len(raw.content),
            )
        )

        rows = provider.parse(raw, trading_date)
        for row in rows:
            if row.benchmark_code != benchmark_code:
                continue
            _, was_created = await price_repo.create_if_not_exists(
                BenchmarkPriceCreate(
                    benchmark_code=row.benchmark_code,
                    trading_date=row.trading_date,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    source="NSE_INDICES_CLOSE_ALL",
                    source_document_id=archive_file.id,
                )
            )
            if was_created:
                summary.rows_created += 1
            else:
                summary.rows_skipped_duplicate += 1

    return summary
