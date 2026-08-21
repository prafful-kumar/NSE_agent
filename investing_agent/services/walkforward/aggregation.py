from __future__ import annotations

"""Pure aggregation over Phase 6D AuditRows: no DB access, no I/O -- mirrors
services/backtesting/report.py's summarize()/group_and_summarize() shape.

Every metric here is computed only over rows with included_in_aggregate=True
(see audit.py's exclusion rules), and, per horizon, only over rows where
that specific horizon's return is non-None -- a PARTIAL-outcome row still
contributes to the horizons it *did* score.

Absolute and benchmark-relative outcomes are always reported side by side
(median/mean stock_return AND median/mean excess_return), per the explicit
instruction not to collapse "did the stock rise" into "did the decision
succeed" -- a BUY that rose 10% while NIFTY rose 30% is a benchmark
underperformance even though it's a positive absolute return.
"""

from collections.abc import Callable, Sequence
from decimal import Decimal

from investing_agent.services.walkforward.audit import AuditRow

_HORIZONS = ("1m", "3m", "6m", "12m")
_HUNDRED = Decimal("100")
BenchmarkKind = str  # PRICE_INDEX | TRI; kept string for CLI/JSON compatibility.


def _excess_return(row: AuditRow, horizon: str, benchmark_kind: BenchmarkKind) -> Decimal | None:
    if benchmark_kind == "TRI":
        return row.excess_return_tri.get(horizon)
    return row.excess_return[horizon]


def holding_age_bucket(days: int | None) -> str:
    if days is None:
        return "UNKNOWN"
    if days < 90:
        return "<3M"
    if days < 182:
        return "3-6M"
    if days < 365:
        return "6-12M"
    if days < 730:
        return "12-24M"
    return "24M+"


def concentration_bucket(pct: Decimal | None) -> str:
    if pct is None:
        return "UNKNOWN"
    if pct < Decimal("0.10"):
        return "<10%"
    if pct < Decimal("0.25"):
        return "10-25%"
    if pct < Decimal("0.50"):
        return "25-50%"
    return "50%+"


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values) / Decimal(len(values))


def _median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal(2)


def _rate_pct(values: Sequence[Decimal], predicate: Callable[[Decimal], bool]) -> Decimal | None:
    if not values:
        return None
    hits = sum(1 for v in values if predicate(v))
    return (Decimal(hits) / Decimal(len(values))) * _HUNDRED


def group_by(rows: Sequence[AuditRow], key_fn: Callable[[AuditRow], str]) -> dict[str, list[AuditRow]]:
    groups: dict[str, list[AuditRow]] = {}
    for r in rows:
        groups.setdefault(key_fn(r), []).append(r)
    return groups


def decision_dollar_impact(row: AuditRow, horizon: str) -> Decimal | None:
    """The marginal effect of this exact trade, in rupees: (shares the
    ACTUAL decision added/removed vs. the HOLD_BASELINE quantity) times the
    per-share price move over the horizon. This is the only sound way to
    compare ACTUAL against HOLD_BASELINE -- score_outcome's stock_return is
    computed purely from (symbol, decision_at) and is therefore always
    identical between the two decision sources for the same event; diffing
    it would always yield zero. quantity_delta captures the actual
    behavioral difference instead."""
    if row.hold_quantity_held is None or row.entry_price is None:
        return None
    stock_ret = row.stock_return[horizon]
    if stock_ret is None:
        return None
    quantity_delta = row.quantity_held - row.hold_quantity_held
    price_move_per_share = row.entry_price * stock_ret
    return quantity_delta * price_move_per_share


def summarize_horizon(
    rows: Sequence[AuditRow], horizon: str, benchmark_kind: BenchmarkKind = "PRICE_INDEX"
) -> dict:
    included = [r for r in rows if r.included_in_aggregate]
    stock = [r.stock_return[horizon] for r in included if r.stock_return[horizon] is not None]
    excess = [
        value for r in included if (value := _excess_return(r, horizon, benchmark_kind)) is not None
    ]
    impacts = [
        v for r in included if (v := decision_dollar_impact(r, horizon)) is not None
    ]

    return {
        "n_included": len(included),
        "n_excluded": len(rows) - len(included),
        "n_scored_this_horizon": len(stock),
        "n_unscored_this_horizon": len(included) - len(stock),
        "median_stock_return_pct": (m * _HUNDRED) if (m := _median(stock)) is not None else None,
        "mean_stock_return_pct": (m * _HUNDRED) if (m := _mean(stock)) is not None else None,
        "median_excess_return_pct": (m * _HUNDRED) if (m := _median(excess)) is not None else None,
        "mean_excess_return_pct": (m * _HUNDRED) if (m := _mean(excess)) is not None else None,
        "positive_return_rate_pct": _rate_pct(stock, lambda v: v > 0),
        "benchmark_outperform_rate_pct": _rate_pct(excess, lambda v: v > 0),
        "decision_dollar_impact_median": _median(impacts),
        "decision_dollar_impact_mean": _mean(impacts),
        "decision_dollar_impact_positive_rate_pct": _rate_pct(impacts, lambda v: v > 0),
        "decision_dollar_impact_n": len(impacts),
    }


def aggregate_by(
    rows: Sequence[AuditRow],
    key_fn: Callable[[AuditRow], str],
    horizon: str,
    benchmark_kind: BenchmarkKind = "PRICE_INDEX",
) -> dict[str, dict]:
    return {
        key: summarize_horizon(group, horizon, benchmark_kind)
        for key, group in group_by(rows, key_fn).items()
    }


def drawdown_distribution(rows: Sequence[AuditRow]) -> dict:
    values = [
        r.max_drawdown_pct * _HUNDRED
        for r in rows
        if r.included_in_aggregate and r.max_drawdown_pct is not None
    ]
    if not values:
        return {"n": 0, "median_pct": None, "mean_pct": None, "worst_pct": None, "best_pct": None}
    return {
        "n": len(values),
        "median_pct": _median(values),
        "mean_pct": _mean(values),
        "worst_pct": min(values),
        "best_pct": max(values),
    }


def data_quality_counts(rows: Sequence[AuditRow]) -> dict:
    counts: dict[str, int] = {}
    for r in rows:
        counts.setdefault(f"data_quality:{r.data_quality_status}", 0)
        counts[f"data_quality:{r.data_quality_status}"] += 1
        counts.setdefault(f"outcome_status:{r.outcome_status}", 0)
        counts[f"outcome_status:{r.outcome_status}"] += 1
        reason_key = f"exclusion:{r.exclusion_reason or 'INCLUDED'}"
        counts.setdefault(reason_key, 0)
        counts[reason_key] += 1
    return counts


def _build_horizon_report(rows: Sequence[AuditRow], benchmark_kind: BenchmarkKind) -> dict[str, dict]:
    by_horizon: dict[str, dict] = {}
    for horizon in _HORIZONS:
        by_horizon[horizon] = {
            "overall": summarize_horizon(rows, horizon, benchmark_kind),
            "by_action": aggregate_by(rows, lambda r: r.action, horizon, benchmark_kind),
            "by_year": aggregate_by(rows, lambda r: str(r.calendar_year), horizon, benchmark_kind),
            "by_holding_age": aggregate_by(
                rows, lambda r: holding_age_bucket(r.holding_age_days), horizon, benchmark_kind
            ),
            "by_concentration": aggregate_by(
                rows, lambda r: concentration_bucket(r.concentration_pct), horizon, benchmark_kind
            ),
        }
    return by_horizon


def build_report(rows: Sequence[AuditRow]) -> dict:
    return {
        "n_events_total": len(rows),
        "n_events_included": sum(1 for r in rows if r.included_in_aggregate),
        # Backward-compatible price-index report.
        "by_horizon": _build_horizon_report(rows, "PRICE_INDEX"),
        "by_horizon_tri": _build_horizon_report(rows, "TRI"),
        "drawdown_distribution": drawdown_distribution(rows),
        "data_quality_counts": data_quality_counts(rows),
    }
