from __future__ import annotations

"""Phase 6E: descriptive, auditable personal decision learning.

This module is read-only over Phase 6D's frozen decisions/outcomes.  It
creates no weights, learns no thresholds, and is deliberately not imported by
the agent graph or recommendation code.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable, Literal, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.services.walkforward.aggregation import (
    concentration_bucket,
    decision_dollar_impact,
    holding_age_bucket,
)
from investing_agent.services.walkforward.audit import AuditRow

HORIZONS = ("1m", "3m", "6m", "12m")
Population = Literal["FULL_HISTORY", "EXCLUDING_2020"]
SampleLabel = Literal[
    "INSUFFICIENT_EVIDENCE", "LOW_SAMPLE", "MODERATE_SAMPLE", "STRONGER_SAMPLE"
]


def sample_label(n: int) -> SampleLabel:
    """A descriptive sample-size label, never a significance claim."""
    if n < 5:
        return "INSUFFICIENT_EVIDENCE"
    if n < 10:
        return "LOW_SAMPLE"
    if n < 30:
        return "MODERATE_SAMPLE"
    return "STRONGER_SAMPLE"


@dataclass(frozen=True)
class PersonalPolicyStats:
    """One horizon's metrics for a group of validated Phase 6D events."""

    n: int
    sample_label: SampleLabel
    median_absolute_return_pct: Decimal | None
    median_excess_return_pct: Decimal | None
    benchmark_outperformance_rate_pct: Decimal | None
    positive_return_rate_pct: Decimal | None
    median_drawdown_pct: Decimal | None
    max_drawdown_pct: Decimal | None
    median_decision_dollar_impact: Decimal | None
    positive_dollar_impact_rate_pct: Decimal | None
    n_decision_dollar_impact: int


@dataclass(frozen=True)
class PersonalDecisionPattern:
    """An auditable descriptive pattern, not an investment policy instruction."""

    population: Population
    dimension: str
    bucket: str
    horizon: str
    benchmark_kind: str
    stats: PersonalPolicyStats
    decision_ids: tuple[str, ...]


@dataclass(frozen=True)
class ImpactEvent:
    """A ranked event retaining the trace to its Phase 6D evidence."""

    symbol: str
    decision_at: date
    action: str
    horizon: str
    decision_dollar_impact: Decimal
    decision_id: str
    hold_decision_id: str | None
    trade_evidence: tuple[dict[str, Any], ...]


def _median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _rate(values: Sequence[Decimal], predicate: Callable[[Decimal], bool]) -> Decimal | None:
    if not values:
        return None
    return Decimal(sum(predicate(value) for value in values)) * 100 / Decimal(len(values))


def _stats(
    rows: Sequence[AuditRow], horizon: str, benchmark_kind: str
) -> PersonalPolicyStats:
    # Excluded Phase 6D events are never treated as learning evidence.
    scored = [
        row for row in rows if row.included_in_aggregate and row.stock_return[horizon] is not None
    ]
    returns = [row.stock_return[horizon] for row in scored]
    excess_source = (
        (lambda row: row.excess_return_tri.get(horizon))
        if benchmark_kind == "TRI"
        else (lambda row: row.excess_return[horizon])
    )
    excess = [
        excess_source(row)
        for row in scored
        if excess_source(row) is not None
    ]
    drawdowns = [row.max_drawdown_pct for row in scored if row.max_drawdown_pct is not None]
    impacts = [
        impact for row in scored if (impact := decision_dollar_impact(row, horizon)) is not None
    ]
    hundred = Decimal(100)
    return PersonalPolicyStats(
        n=len(scored),
        sample_label=sample_label(len(scored)),
        median_absolute_return_pct=(_median(returns) * hundred if returns else None),
        median_excess_return_pct=(_median(excess) * hundred if excess else None),
        benchmark_outperformance_rate_pct=_rate(excess, lambda value: value > 0),
        positive_return_rate_pct=_rate(returns, lambda value: value > 0),
        median_drawdown_pct=(_median(drawdowns) * hundred if drawdowns else None),
        # Drawdowns are non-positive; the smallest value is the maximum loss.
        max_drawdown_pct=(min(drawdowns) * hundred if drawdowns else None),
        median_decision_dollar_impact=_median(impacts),
        positive_dollar_impact_rate_pct=_rate(impacts, lambda value: value > 0),
        n_decision_dollar_impact=len(impacts),
    )


async def derive_market_regimes(
    session: AsyncSession, rows: Sequence[AuditRow]
) -> dict[str, str]:
    """Classify dates from trailing NIFTY 50 price action only.

    Fixed, pre-declared boundaries use the closest closes at/before the
    decision date and 180 calendar days earlier: >=10% STRONG_UPTREND,
    <=-10% DRAWDOWN, otherwise WEAK_OR_SIDEWAYS.  No future price is read.
    """
    from investing_agent.db.repositories.prices import BenchmarkPriceRepository

    repo = BenchmarkPriceRepository(session)
    by_date: dict[date, str] = {}
    labels: dict[str, str] = {}
    for row in rows:
        if row.decision_at not in by_date:
            current = await repo.get_nearest_on_or_before("NIFTY_50", row.decision_at)
            prior = await repo.get_nearest_on_or_before(
                "NIFTY_50", row.decision_at - timedelta(days=180)
            )
            if current is None or prior is None or prior.close == 0:
                by_date[row.decision_at] = "UNKNOWN"
            else:
                trailing_return = current.close / prior.close - 1
                by_date[row.decision_at] = (
                    "STRONG_UPTREND" if trailing_return >= Decimal("0.10")
                    else "DRAWDOWN" if trailing_return <= Decimal("-0.10")
                    else "WEAK_OR_SIDEWAYS"
                )
        labels[str(row.decision_id)] = by_date[row.decision_at]
    return labels


def build_patterns(
    rows: Sequence[AuditRow],
    *,
    sectors_by_symbol: dict[str, str | None],
    regimes_by_decision_id: dict[str, str],
    benchmark_kind: str = "PRICE_INDEX",
) -> list[PersonalDecisionPattern]:
    """Build full-history, excluding-2020, and dimension-specific patterns."""
    dimensions: tuple[tuple[str, Callable[[AuditRow], str]], ...] = (
        ("overall", lambda _row: "ALL"),
        ("action", lambda row: row.action),
        ("calendar_year", lambda row: str(row.calendar_year)),
        ("holding_age", lambda row: holding_age_bucket(row.holding_age_days)),
        ("concentration", lambda row: concentration_bucket(row.concentration_pct)),
        ("market_regime", lambda row: regimes_by_decision_id.get(str(row.decision_id), "UNKNOWN")),
        ("symbol", lambda row: row.symbol),
        ("sector", lambda row: sectors_by_symbol.get(row.symbol) or "UNKNOWN"),
    )
    populations: tuple[tuple[Population, Sequence[AuditRow]], ...] = (
        ("FULL_HISTORY", rows),
        ("EXCLUDING_2020", [row for row in rows if row.calendar_year != 2020]),
    )
    patterns: list[PersonalDecisionPattern] = []
    for population, population_rows in populations:
        for dimension, key_fn in dimensions:
            groups: dict[str, list[AuditRow]] = defaultdict(list)
            for row in population_rows:
                groups[key_fn(row)].append(row)
            for bucket, group in sorted(groups.items()):
                for horizon in HORIZONS:
                    stats = _stats(group, horizon, benchmark_kind)
                    # Stock/sector patterns only exist when enough events are
                    # scored; all other small buckets remain visible as such.
                    if dimension in {"symbol", "sector"} and stats.n < 5:
                        continue
                    patterns.append(PersonalDecisionPattern(
                        population=population,
                        dimension=dimension,
                        bucket=bucket,
                        horizon=horizon,
                        benchmark_kind=benchmark_kind,
                        stats=stats,
                        decision_ids=tuple(str(row.decision_id) for row in group),
                    ))
    return patterns


def ranked_impacts(
    rows: Sequence[AuditRow], horizon: str, limit: int = 5
) -> tuple[list[ImpactEvent], list[ImpactEvent]]:
    events: list[ImpactEvent] = []
    for row in rows:
        if not row.included_in_aggregate:
            continue
        impact = decision_dollar_impact(row, horizon)
        if impact is None:
            continue
        events.append(ImpactEvent(
            symbol=row.symbol,
            decision_at=row.decision_at,
            action=row.action,
            horizon=horizon,
            decision_dollar_impact=impact,
            decision_id=str(row.decision_id),
            hold_decision_id=str(row.hold_decision_id) if row.hold_decision_id else None,
            trade_evidence=tuple(row.trade_evidence),
        ))
    positive = sorted(
        (event for event in events if event.decision_dollar_impact > 0),
        key=lambda event: event.decision_dollar_impact,
        reverse=True,
    )[:limit]
    negative = sorted(
        (event for event in events if event.decision_dollar_impact < 0),
        key=lambda event: event.decision_dollar_impact,
    )[:limit]
    return positive, negative
