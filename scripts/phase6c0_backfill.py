from __future__ import annotations

"""One-time Phase 6C.0 production backfill driver.

Syncs daily EQ prices for every symbol appearing in the user's real Zerodha
historical_trades table (2020-01-01 -> today) plus the NIFTY 50 benchmark
for the same range, against the real production database.

Chunked by calendar year and committed per chunk (not one giant transaction)
so a mid-run block/crash preserves prior progress -- natural-key idempotency
on DailyPrice/BenchmarkPrice means re-running this script is always safe and
never duplicates rows. Uses sync_daily_prices_multi so the equity bhavcopy
for a given day is fetched exactly once regardless of symbol count.

Not a permanent CLI command -- this is an operational one-off, run directly
via `python scripts/phase6c0_backfill.py`.
"""

import asyncio
from datetime import date

from sqlalchemy import text

from investing_agent.db.session import AsyncSessionLocal
from investing_agent.services.prices.ingestion import (
    PriceSyncSummary,
    sync_benchmark_prices,
    sync_daily_prices_multi,
)
from investing_agent.services.prices.nse_bhavcopy import NSEBhavcopyHistoricalPriceProvider
from investing_agent.services.prices.nse_indices import NSEIndicesBenchmarkPriceProvider

START_DATE = date(2020, 1, 1)
END_DATE = date.today()


def _year_chunks(start: date, end: date) -> list[tuple[date, date]]:
    chunks = []
    year = start.year
    while year <= end.year:
        chunk_start = max(start, date(year, 1, 1))
        chunk_end = min(end, date(year, 12, 31))
        chunks.append((chunk_start, chunk_end))
        year += 1
    return chunks


def _merge(total: PriceSyncSummary, part: PriceSyncSummary) -> None:
    total.dates_checked += part.dates_checked
    total.dates_no_data += part.dates_no_data
    total.rows_created += part.rows_created
    total.rows_skipped_duplicate += part.rows_skipped_duplicate
    total.errors.extend(part.errors)


async def main() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("select distinct symbol from historical_trades order by symbol"))
        symbols = [row[0] for row in result.fetchall()]
    print(f"symbols requested ({len(symbols)}): {symbols}")

    equity_total = PriceSyncSummary()
    equity_provider = NSEBhavcopyHistoricalPriceProvider()
    blocked = False
    try:
        for chunk_start, chunk_end in _year_chunks(START_DATE, END_DATE):
            async with AsyncSessionLocal() as session:
                part = await sync_daily_prices_multi(
                    session, symbols, chunk_start, chunk_end, equity_provider
                )
                await session.commit()
            _merge(equity_total, part)
            print(
                f"[equities {chunk_start}..{chunk_end}] "
                f"dates_checked={part.dates_checked} dates_no_data={part.dates_no_data} "
                f"rows_created={part.rows_created} rows_skipped_duplicate={part.rows_skipped_duplicate} "
                f"errors={len(part.errors)}"
            )
            if any("blocked" in e for e in part.errors):
                print("Blocked by source -- stopping equity backfill early.")
                blocked = True
                break
    finally:
        await equity_provider.aclose()

    print("=== EQUITY TOTAL ===")
    print(equity_total)

    benchmark_total = PriceSyncSummary()
    benchmark_provider = NSEIndicesBenchmarkPriceProvider("NIFTY_50")
    try:
        for chunk_start, chunk_end in _year_chunks(START_DATE, END_DATE):
            async with AsyncSessionLocal() as session:
                part = await sync_benchmark_prices(
                    session, "NIFTY_50", chunk_start, chunk_end, benchmark_provider
                )
                await session.commit()
            _merge(benchmark_total, part)
            print(
                f"[benchmark {chunk_start}..{chunk_end}] "
                f"dates_checked={part.dates_checked} dates_no_data={part.dates_no_data} "
                f"rows_created={part.rows_created} rows_skipped_duplicate={part.rows_skipped_duplicate} "
                f"errors={len(part.errors)}"
            )
            if any("blocked" in e for e in part.errors):
                print("Blocked by source -- stopping benchmark backfill early.")
                blocked = True
                break
    finally:
        await benchmark_provider.aclose()

    print("=== BENCHMARK TOTAL ===")
    print(benchmark_total)
    print("=== DONE ===", "blocked" if blocked else "completed")


if __name__ == "__main__":
    asyncio.run(main())
