from __future__ import annotations

"""Pure aggregation functions over BacktestScore rows: MAE/MAPE per metric,
EBITDA-margin error, within-band hit rates, surprise-direction distribution,
growth-direction accuracy, and sample sizes.

summarize() computes one flat summary dict; group_and_summarize() buckets
scores by an arbitrary key function first (company symbol, sector, model
version, confidence bucket) and summarizes each bucket — this one function
covers every "performance by X" breakdown the report needs.
"""

from collections.abc import Callable, Sequence
from decimal import Decimal

from investing_agent.schemas.backtesting import BacktestScoreRead

_DIRECTIONS = ("beat", "inline", "miss")


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values) / Decimal(len(values))


def _mean_abs(scores: Sequence[BacktestScoreRead], field: str) -> tuple[Decimal | None, int]:
    values = [abs(v) for s in scores if (v := getattr(s, field)) is not None]
    return _mean(values), len(values)


def _hit_rate_pct(scores: Sequence[BacktestScoreRead], field: str) -> tuple[Decimal | None, int]:
    values = [v for s in scores if (v := getattr(s, field)) is not None]
    if not values:
        return None, 0
    hits = sum(1 for v in values if v)
    return (Decimal(hits) / Decimal(len(values))) * 100, len(values)


def _surprise_direction_distribution_pct(scores: Sequence[BacktestScoreRead]) -> dict[str, Decimal]:
    values = [s.surprise_direction for s in scores if s.surprise_direction is not None]
    if not values:
        return {}
    total = Decimal(len(values))
    return {d: (Decimal(values.count(d)) / total) * 100 for d in _DIRECTIONS if d in values}


def summarize(scores: Sequence[BacktestScoreRead]) -> dict:
    # UNSCORABLE rows (no verified history — see runner.py) are excluded
    # from every accuracy metric below: their numeric fields are already
    # all None so _mean_abs/_hit_rate_pct would skip them anyway, but
    # excluding them explicitly from the denominator keeps "n" (and
    # unscorable_n) an honest sample-size signal rather than something a
    # reader has to cross-reference against per-field n's to interpret.
    scored = [s for s in scores if s.status == "SCORED"]
    unscorable_n = len(scores) - len(scored)

    revenue_mape, revenue_mape_n = _mean_abs(scored, "revenue_error_pct")
    pat_mape, pat_mape_n = _mean_abs(scored, "pat_error_pct")
    eps_mape, eps_mape_n = _mean_abs(scored, "eps_error_pct")
    margin_mae, margin_mae_n = _mean_abs(scored, "margin_error_bps")
    revenue_hit, revenue_hit_n = _hit_rate_pct(scored, "within_band_revenue")
    pat_hit, pat_hit_n = _hit_rate_pct(scored, "within_band_pat")
    eps_hit, eps_hit_n = _hit_rate_pct(scored, "within_band_eps")
    direction_accuracy, direction_n = _hit_rate_pct(scored, "growth_direction_correct")

    return {
        "n": len(scores),
        "unscorable_n": unscorable_n,
        "revenue_mape_pct": revenue_mape, "revenue_mape_n": revenue_mape_n,
        "pat_mape_pct": pat_mape, "pat_mape_n": pat_mape_n,
        "eps_mape_pct": eps_mape, "eps_mape_n": eps_mape_n,
        "margin_mae_bps": margin_mae, "margin_mae_n": margin_mae_n,
        "revenue_within_band_pct": revenue_hit, "revenue_within_band_n": revenue_hit_n,
        "pat_within_band_pct": pat_hit, "pat_within_band_n": pat_hit_n,
        "eps_within_band_pct": eps_hit, "eps_within_band_n": eps_hit_n,
        "growth_direction_accuracy_pct": direction_accuracy, "growth_direction_n": direction_n,
        "surprise_direction_distribution_pct": _surprise_direction_distribution_pct(scored),
    }


def group_and_summarize(
    scores: Sequence[BacktestScoreRead], key_fn: Callable[[BacktestScoreRead], str]
) -> dict[str, dict]:
    groups: dict[str, list[BacktestScoreRead]] = {}
    for s in scores:
        groups.setdefault(key_fn(s), []).append(s)
    return {key: summarize(rows) for key, rows in groups.items()}
