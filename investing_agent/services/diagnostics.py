from __future__ import annotations

"""Descriptive (non-fitted) margin/tax seasonality diagnostics computed
directly from verified quarterly FinancialResult figures.

This exists to characterize a company's actual seasonal pattern -- e.g. a
fiscal-year-end revenue/margin push, or a swinging effective tax rate --
from real transcribed history before deciding whether the estimator
(services/estimation/deterministic.py) should account for it. It only ever
reports averages per fiscal-quarter label (Q1-Q4); it never fits a
coefficient and is not consumed by the estimator.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

_QUANT = Decimal("0.01")


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values) / Decimal(len(values))).quantize(_QUANT)


@dataclass(frozen=True)
class QuarterlyFinancialPoint:
    quarter: str  # "Q1".."Q4"
    fiscal_year: int
    period_label: str
    revenue: Decimal | None
    pbt: Decimal | None
    pat: Decimal | None
    tax_expense: Decimal | None


def compute_quarter_seasonality(points: Sequence[QuarterlyFinancialPoint]) -> dict[str, dict]:
    """Groups points by quarter label (Q1-Q4) and returns, per quarter:
    n, avg_pbt_margin_pct, avg_pat_margin_pct, avg_effective_tax_rate_pct.

    A point missing a field needed for one ratio (e.g. tax_expense not
    disclosed that quarter) is skipped only for that ratio, not dropped
    from the group entirely -- one quarter's disclosure gap shouldn't
    shrink the sample for a metric it did report.
    """
    groups: dict[str, list[QuarterlyFinancialPoint]] = {}
    for p in points:
        groups.setdefault(p.quarter, []).append(p)

    result: dict[str, dict] = {}
    for quarter, pts in groups.items():
        pbt_margins = [
            (p.pbt / p.revenue) * 100 for p in pts
            if p.pbt is not None and p.revenue
        ]
        pat_margins = [
            (p.pat / p.revenue) * 100 for p in pts
            if p.pat is not None and p.revenue
        ]
        tax_rates = [
            (p.tax_expense / p.pbt) * 100 for p in pts
            if p.tax_expense is not None and p.pbt
        ]
        result[quarter] = {
            "n": len(pts),
            "avg_pbt_margin_pct": _mean(pbt_margins),
            "avg_pat_margin_pct": _mean(pat_margins),
            "avg_effective_tax_rate_pct": _mean(tax_rates),
        }
    return result
