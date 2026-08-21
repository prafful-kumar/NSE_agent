from __future__ import annotations

"""Offline Phase 6F candidate-policy construction and validation.

The functions here operate on already-frozen Phase 6D audit rows.  They do
not import the agent graph and cannot modify live recommendations.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Sequence

from investing_agent.services.walkforward.aggregation import decision_dollar_impact
from investing_agent.services.walkforward.audit import AuditRow

HORIZON = "12m"
MIN_N = 5
MAX_SINGLE_SYMBOL_SHARE = Decimal("0.50")
MAX_DRAWNDOWN_DEGRADATION_PCT = Decimal("10")


@dataclass(frozen=True)
class CandidateSpec:
    rule_id: str
    affected_action: str
    condition: dict[str, str]
    adjustment: dict[str, str]
    matches: Callable[[AuditRow], bool]
    complexity: int = 1


@dataclass(frozen=True)
class Metrics:
    n: int
    median_stock_return_pct: Decimal | None
    median_benchmark_return_pct: Decimal | None
    median_excess_return_pct: Decimal | None
    median_decision_dollar_impact: Decimal | None
    hit_rate_pct: Decimal | None
    benchmark_outperformance_rate_pct: Decimal | None
    median_drawdown_pct: Decimal | None
    turnover_inr: Decimal
    median_concentration_pct: Decimal | None
    interventions: int


@dataclass(frozen=True)
class ValidationFold:
    train_end_year: int
    validation_year: int
    train: Metrics
    validation: Metrics


@dataclass(frozen=True)
class CandidateEvaluation:
    spec: CandidateSpec
    full_history: Metrics
    excluding_2020: Metrics
    folds: tuple[ValidationFold, ...]
    status: str
    confidence: Decimal | None
    rejection_reasons: tuple[str, ...]


def candidate_specs() -> tuple[CandidateSpec, ...]:
    """Three fixed, intelligible candidates; no threshold fitting occurs."""
    return (
        CandidateSpec(
            rule_id="add_after_6_to_12m_review",
            affected_action="ADD",
            condition={"action": "ADD", "holding_age_days": "[182,365)"},
            adjustment={"type": "CONFIDENCE_REVIEW", "effect": "review ADD preference"},
            matches=lambda row: row.action == "ADD" and row.holding_age_days is not None and 182 <= row.holding_age_days < 365,
        ),
        CandidateSpec(
            rule_id="long_held_thesis_review",
            affected_action="HOLDING_REVIEW",
            condition={"holding_age_days": ">=730"},
            adjustment={"type": "THESIS_REVIEW_FLAG", "effect": "raise review urgency only"},
            matches=lambda row: row.holding_age_days is not None and row.holding_age_days >= 730,
        ),
        CandidateSpec(
            rule_id="high_concentration_add_penalty",
            affected_action="ADD",
            condition={"action": "ADD", "concentration_pct": ">=0.50"},
            adjustment={"type": "CONCENTRATION_PENALTY", "effect": "propose HOLD of incremental shares"},
            matches=lambda row: row.action == "ADD" and row.concentration_pct is not None and row.concentration_pct >= Decimal("0.50"),
        ),
    )


def _median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _pct(values: Sequence[Decimal], predicate: Callable[[Decimal], bool]) -> Decimal | None:
    if not values:
        return None
    return Decimal(sum(predicate(value) for value in values)) * 100 / Decimal(len(values))


def summarize(rows: Sequence[AuditRow], spec: CandidateSpec) -> Metrics:
    """Actual-decision outcomes for a candidate's pre-declared cohort.

    HOLD is the counterfactual already represented by decision_dollar_impact:
    zero incremental shares equals zero incremental INR impact.  There are no
    frozen AGENT decisions in Phase 6D v1, so a current-policy comparator is
    explicitly unavailable rather than fabricated.
    """
    matched = [
        row for row in rows
        if row.included_in_aggregate and spec.matches(row) and row.stock_return[HORIZON] is not None
    ]
    excess = [row.excess_return[HORIZON] for row in matched if row.excess_return[HORIZON] is not None]
    returns = [row.stock_return[HORIZON] for row in matched if row.stock_return[HORIZON] is not None]
    benchmark = [
        row.benchmark_return[HORIZON]
        for row in matched
        if row.benchmark_return[HORIZON] is not None
    ]
    impacts = [impact for row in matched if (impact := decision_dollar_impact(row, HORIZON)) is not None]
    drawdowns = [row.max_drawdown_pct for row in matched if row.max_drawdown_pct is not None]
    concentrations = [row.concentration_pct for row in matched if row.concentration_pct is not None]
    turnover = sum(
        abs(row.quantity_held - row.hold_quantity_held) * row.entry_price
        for row in matched
        if row.hold_quantity_held is not None and row.entry_price is not None
    )
    return Metrics(
        n=len(matched),
        median_stock_return_pct=(_median(returns) * 100 if returns else None),
        median_benchmark_return_pct=(_median(benchmark) * 100 if benchmark else None),
        median_excess_return_pct=(_median(excess) * 100 if excess else None),
        median_decision_dollar_impact=_median(impacts),
        hit_rate_pct=_pct(returns, lambda value: value > 0),
        benchmark_outperformance_rate_pct=_pct(excess, lambda value: value > 0),
        median_drawdown_pct=(_median(drawdowns) * 100 if drawdowns else None),
        turnover_inr=turnover,
        median_concentration_pct=(_median(concentrations) * 100 if concentrations else None),
        interventions=len(matched),
    )


def validate_expanding(rows: Sequence[AuditRow], spec: CandidateSpec) -> CandidateEvaluation:
    """Use only earlier years to form a candidate and later years to test it."""
    full_history = summarize(rows, spec)
    excluding_2020 = summarize([row for row in rows if row.calendar_year != 2020], spec)
    years = sorted({row.calendar_year for row in rows})
    folds: list[ValidationFold] = []
    for validation_year in years:
        train = [row for row in rows if row.calendar_year < validation_year]
        validation = [row for row in rows if row.calendar_year == validation_year]
        if not train or not validation:
            continue
        folds.append(ValidationFold(
            train_end_year=validation_year - 1,
            validation_year=validation_year,
            train=summarize(train, spec),
            validation=summarize(validation, spec),
        ))

    reasons: list[str] = []
    valid_folds = [fold for fold in folds if fold.train.n >= MIN_N and fold.validation.n >= MIN_N]
    if full_history.n < MIN_N:
        reasons.append("minimum full-history sample not met")
    if excluding_2020.n < MIN_N:
        reasons.append("minimum excluding-2020 sample not met")
    if len(valid_folds) < 2:
        reasons.append("fewer than two expanding out-of-sample folds meet minimum n")
    if excluding_2020.median_excess_return_pct is None:
        reasons.append("no excluding-2020 benchmark-relative result")
    elif excluding_2020.median_excess_return_pct <= 0:
        reasons.append("no positive excluding-2020 median excess return")

    matched = [row for row in rows if row.included_in_aggregate and spec.matches(row)]
    symbol_counts: dict[str, int] = {}
    for row in matched:
        symbol_counts[row.symbol] = symbol_counts.get(row.symbol, 0) + 1
    if matched and max(symbol_counts.values()) / len(matched) > MAX_SINGLE_SYMBOL_SHARE:
        reasons.append("evidence depends on one symbol")

    for fold in valid_folds:
        train_dd = fold.train.median_drawdown_pct
        validation_dd = fold.validation.median_drawdown_pct
        if train_dd is not None and validation_dd is not None and validation_dd < train_dd - MAX_DRAWNDOWN_DEGRADATION_PCT:
            reasons.append(f"catastrophic drawdown degradation in validation {fold.validation_year}")
            break

    # Fixed complexity penalty: one simple condition must clear a 1% median
    # excess-return hurdle; no candidate gets to fit this hurdle to the data.
    if excluding_2020.median_excess_return_pct is not None and excluding_2020.median_excess_return_pct <= Decimal(spec.complexity):
        reasons.append("benefit does not clear fixed complexity penalty")
    status = "BACKTESTED" if not reasons else "REJECTED"
    confidence = min(Decimal("0.90"), Decimal(full_history.n) / Decimal(100)) if status == "BACKTESTED" else None
    return CandidateEvaluation(
        spec=spec,
        full_history=full_history,
        excluding_2020=excluding_2020,
        folds=tuple(folds),
        status=status,
        confidence=confidence,
        rejection_reasons=tuple(reasons),
    )


def metrics_as_dict(metrics: Metrics) -> dict[str, str | int | None]:
    """JSON-safe audit representation (Decimals are strings, never floats)."""
    return {
        "n": metrics.n,
        "median_stock_return_pct": str(metrics.median_stock_return_pct) if metrics.median_stock_return_pct is not None else None,
        "median_benchmark_return_pct": str(metrics.median_benchmark_return_pct) if metrics.median_benchmark_return_pct is not None else None,
        "median_excess_return_pct": str(metrics.median_excess_return_pct) if metrics.median_excess_return_pct is not None else None,
        "median_decision_dollar_impact": str(metrics.median_decision_dollar_impact) if metrics.median_decision_dollar_impact is not None else None,
        "hit_rate_pct": str(metrics.hit_rate_pct) if metrics.hit_rate_pct is not None else None,
        "benchmark_outperformance_rate_pct": str(metrics.benchmark_outperformance_rate_pct) if metrics.benchmark_outperformance_rate_pct is not None else None,
        "median_drawdown_pct": str(metrics.median_drawdown_pct) if metrics.median_drawdown_pct is not None else None,
        "turnover_inr": str(metrics.turnover_inr),
        "median_concentration_pct": str(metrics.median_concentration_pct) if metrics.median_concentration_pct is not None else None,
        "interventions": metrics.interventions,
        "current_deterministic_policy": "UNAVAILABLE_NO_FROZEN_AGENT_DECISIONS",
        "hold_baseline_incremental_impact_inr": "0",
    }
