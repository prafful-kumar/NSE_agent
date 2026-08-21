from __future__ import annotations

"""Price resolution for Phase 6C walk-forward outcomes.

The only module in this feature allowed to import DailyPriceRepository /
BenchmarkPriceRepository (aside from outcomes.py which calls into it).
freeze_decision (decisions.py) never imports this module at all -- that
physical separation is what makes "no future price leakage" a structural
property rather than a convention, and is what the leakage tests assert
against (spying on these repositories during freeze_decision and asserting
zero calls).

Every resolver returns a ResolvedPrice with price=None and a specific,
distinguishing reason whenever a real value can't be produced -- never a
fabricated number. INSUFFICIENT_HORIZON (target date is beyond the latest
data this system has for the symbol/benchmark at all) is kept distinct from
DATA_GAP (a mid-series hole -- data exists further out, but nothing within
the bounded lookahead window around the target date).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.repositories.prices import BenchmarkPriceRepository, DailyPriceRepository

DEFAULT_MAX_LOOKAHEAD_DAYS = 10


@dataclass(frozen=True)
class ResolvedPrice:
    price: Decimal | None
    trading_date: date | None
    reason: str | None


async def resolve_entry_price(session: AsyncSession, symbol: str, decision_at: date) -> ResolvedPrice:
    """Nearest EQ-series close on or before decision_at -- always PIT-safe
    by construction since it never looks forward of decision_at."""
    repo = DailyPriceRepository(session)
    row = await repo.get_nearest_on_or_before(symbol, decision_at)
    if row is None:
        return ResolvedPrice(None, None, f"DATA_GAP: no price on or before {decision_at}")
    return ResolvedPrice(row.close, row.trading_date, None)


async def resolve_horizon_price(
    session: AsyncSession,
    symbol: str,
    target_date: date,
    max_lookahead_days: int = DEFAULT_MAX_LOOKAHEAD_DAYS,
) -> ResolvedPrice:
    repo = DailyPriceRepository(session)
    row = await repo.get_nearest_on_or_after(symbol, target_date, max_lookahead_days)
    if row is not None:
        return ResolvedPrice(row.close, row.trading_date, None)

    latest = await repo.get_latest_trading_date(symbol)
    if latest is None or latest < target_date:
        return ResolvedPrice(
            None, None, f"INSUFFICIENT_HORIZON: target date {target_date} is after latest available data"
        )
    return ResolvedPrice(
        None, None, f"DATA_GAP: no price within {max_lookahead_days} days of {target_date}"
    )


async def resolve_benchmark_entry_price(
    session: AsyncSession, benchmark_code: str, decision_at: date
) -> ResolvedPrice:
    repo = BenchmarkPriceRepository(session)
    row = await repo.get_nearest_on_or_before(benchmark_code, decision_at)
    if row is None:
        return ResolvedPrice(None, None, f"DATA_GAP: no benchmark price on or before {decision_at}")
    return ResolvedPrice(row.close, row.trading_date, None)


async def resolve_benchmark_horizon_price(
    session: AsyncSession,
    benchmark_code: str,
    target_date: date,
    max_lookahead_days: int = DEFAULT_MAX_LOOKAHEAD_DAYS,
) -> ResolvedPrice:
    repo = BenchmarkPriceRepository(session)
    row = await repo.get_nearest_on_or_after(benchmark_code, target_date, max_lookahead_days)
    if row is not None:
        return ResolvedPrice(row.close, row.trading_date, None)

    latest = await repo.get_latest_trading_date(benchmark_code)
    if latest is None or latest < target_date:
        return ResolvedPrice(
            None, None,
            f"INSUFFICIENT_HORIZON: target date {target_date} is after latest available benchmark data",
        )
    return ResolvedPrice(
        None, None, f"DATA_GAP: no benchmark price within {max_lookahead_days} days of {target_date}"
    )
