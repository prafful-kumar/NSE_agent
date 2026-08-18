from __future__ import annotations

"""Pure, deterministic earnings estimator — no I/O, no LLM, fully
unit-testable in isolation from FeatureSnapshotPayload alone.

Design (see the Phase 5A plan for the full rationale):
- Revenue is projected from a growth rate (YoY preferred — anchored to the
  same quarter a year ago when that exact point exists in history; QoQ
  fallback — anchored to the most recent known quarter) applied to an
  anchor value. Fewer than 2 usable growth samples for the chosen method
  means no revenue estimate (None), not a guess.
- EBITDA margin and the derived PAT margin are trailing distributions
  (median +/- stdev of historical ratios), not growth-projected — margins
  don't compound the way revenue does.
- PAT = revenue estimate x historical PAT-margin estimate, and EPS = PAT /
  an implied diluted share count backed out of the most recent quarter's
  pat/eps_diluted — both derived, never independently trended, so the four
  numbers tell one consistent P&L story instead of four contradictory ones.
- Explicit numeric ManagementGuidance (when present for the target fiscal
  year) widens the low/high band to never exclude the guided range, and
  pulls the base case toward the guidance midpoint — but is always recorded
  as an assumption whether or not it changed anything, so a human reviewing
  the estimate can see guidance was considered.
- Confidence is a Python-computed score (data density, growth volatility,
  guidance availability) — the LLM narrator (services/estimation/narrator.py)
  never touches it.
"""

import statistics
from dataclasses import dataclass, field
from decimal import Decimal

from investing_agent.schemas.estimation import (
    AssumptionItem,
    FeatureSnapshotPayload,
    HistoricalFinancialPoint,
)

MIN_GROWTH_SAMPLES = 2
MIN_MARGIN_SAMPLES = 2


@dataclass
class MetricEstimate:
    low: Decimal | None = None
    base: Decimal | None = None
    high: Decimal | None = None


@dataclass
class DeterministicEstimate:
    revenue: MetricEstimate = field(default_factory=MetricEstimate)
    ebitda_margin: MetricEstimate = field(default_factory=MetricEstimate)
    pat: MetricEstimate = field(default_factory=MetricEstimate)
    eps: MetricEstimate = field(default_factory=MetricEstimate)
    confidence: Decimal = Decimal("0.05")
    assumptions: list[AssumptionItem] = field(default_factory=list)


def estimate_deterministic(snapshot: FeatureSnapshotPayload) -> DeterministicEstimate:
    assumptions: list[AssumptionItem] = []
    history = sorted(snapshot.historical_financials, key=lambda p: p.period_end)
    target = snapshot.target_period

    revenue_guidance = _find_guidance(snapshot, "revenue", target.fiscal_year)
    margin_guidance = _find_guidance(snapshot, "margin", target.fiscal_year)

    revenue, revenue_samples_n, revenue_growth_stdev = _estimate_revenue(
        history, target, revenue_guidance, assumptions
    )
    ebitda_margin, margin_samples_n = _estimate_margin(
        history, lambda p: p.ebitda_margin_pct, "EBITDA margin", margin_guidance, assumptions
    )
    pat_margin, _ = _estimate_margin(
        history,
        lambda p: (p.pat / p.revenue * 100) if p.pat is not None and p.revenue else None,
        "PAT margin",
        None,
        assumptions,
    )
    pat = _derive_pat(revenue, pat_margin, assumptions)
    eps = _derive_eps(pat, history, assumptions)

    confidence = _confidence(
        n_history=len(history),
        revenue_samples_n=revenue_samples_n,
        revenue_growth_stdev=revenue_growth_stdev,
        has_guidance=revenue_guidance is not None or margin_guidance is not None,
    )
    assumptions.append(
        AssumptionItem(
            text=(
                f"Confidence {confidence}: based on {len(history)} historical quarter(s), "
                f"{revenue_samples_n} revenue growth sample(s)"
                + (", guidance available" if (revenue_guidance or margin_guidance) else "")
                + "."
            ),
            source="deterministic",
        )
    )

    return DeterministicEstimate(
        revenue=revenue,
        ebitda_margin=ebitda_margin,
        pat=pat,
        eps=eps,
        confidence=confidence,
        assumptions=assumptions,
    )


def _find_guidance(snapshot: FeatureSnapshotPayload, guidance_type: str, fiscal_year: int):
    for g in snapshot.guidance:
        if g.guidance_type == guidance_type and g.fiscal_year == fiscal_year:
            if g.guidance_low is not None and g.guidance_high is not None:
                return g
    return None


def _growth_samples(
    history: list[HistoricalFinancialPoint], value_of, same_quarter_only: bool
) -> list[Decimal]:
    samples: list[Decimal] = []
    if same_quarter_only:
        by_quarter: dict[str | None, list[HistoricalFinancialPoint]] = {}
        for p in history:
            by_quarter.setdefault(p.quarter, []).append(p)
        for points in by_quarter.values():
            points = sorted(points, key=lambda p: p.fiscal_year)
            for a, b in zip(points, points[1:]):
                if b.fiscal_year - a.fiscal_year != 1:
                    continue
                av, bv = value_of(a), value_of(b)
                if av and bv is not None and av != 0:
                    samples.append((bv - av) / av)
    else:
        for a, b in zip(history, history[1:]):
            av, bv = value_of(a), value_of(b)
            if av and bv is not None and av != 0:
                samples.append((bv - av) / av)
    return samples


def _estimate_revenue(
    history: list[HistoricalFinancialPoint], target, guidance, assumptions: list[AssumptionItem]
) -> tuple[MetricEstimate, int, Decimal | None]:
    same_qtr_anchor_point = next(
        (
            p for p in reversed(history)
            if p.fiscal_year == target.fiscal_year - 1 and p.quarter == target.quarter
            and p.revenue is not None
        ),
        None,
    )
    most_recent_point = next((p for p in reversed(history) if p.revenue is not None), None)
    value_of = lambda p: p.revenue  # noqa: E731

    method = None
    anchor = None
    samples: list[Decimal] = []
    if same_qtr_anchor_point is not None:
        yoy_samples = _growth_samples(history, value_of, same_quarter_only=True)
        if len(yoy_samples) >= MIN_GROWTH_SAMPLES:
            method, anchor, samples = "YoY", same_qtr_anchor_point.revenue, yoy_samples

    if method is None and most_recent_point is not None:
        qoq_samples = _growth_samples(history, value_of, same_quarter_only=False)
        if len(qoq_samples) >= MIN_GROWTH_SAMPLES:
            method, anchor, samples = "QoQ", most_recent_point.revenue, qoq_samples

    result = MetricEstimate()
    growth_stdev: Decimal | None = None
    if method is not None:
        median_growth = Decimal(str(statistics.median(samples)))
        growth_stdev = Decimal(str(statistics.stdev(samples))) if len(samples) > 1 else Decimal("0")
        result.base = _clamp_nonneg(anchor * (1 + median_growth))
        result.low = _clamp_nonneg(anchor * (1 + median_growth - growth_stdev))
        result.high = _clamp_nonneg(anchor * (1 + median_growth + growth_stdev))
        assumptions.append(
            AssumptionItem(
                text=(
                    f"Revenue base case: median {method} growth of "
                    f"{(median_growth * 100).quantize(Decimal('0.1'))}% over {len(samples)} "
                    f"comparable period(s), anchored to {anchor}."
                ),
                source="deterministic",
            )
        )
    else:
        assumptions.append(
            AssumptionItem(
                text="Revenue: insufficient historical data (fewer than 2 usable comparable "
                "quarters) — no estimate produced.",
                source="deterministic",
            )
        )

    if guidance is not None:
        # Even when growth stats didn't produce an estimate (e.g. only 1
        # historical quarter), guidance can still rescue a usable range as
        # long as SOME anchor value exists to apply the guided growth rate to.
        fallback_anchor = (
            same_qtr_anchor_point.revenue if same_qtr_anchor_point is not None
            else (most_recent_point.revenue if most_recent_point is not None else None)
        )
        blend_anchor = anchor if anchor is not None else fallback_anchor
        assumptions.append(
            AssumptionItem(
                text=(
                    f"Guidance available: FY{guidance.fiscal_year} {guidance.metric_label} "
                    f"{guidance.guidance_low}-{guidance.guidance_high} "
                    f"({guidance.period_label or 'unlabeled period'}); "
                    f"{'blended into' if blend_anchor is not None else 'could not be blended into'} "
                    "the revenue range."
                ),
                source="deterministic",
            )
        )
        if blend_anchor is not None:
            gl = _clamp_nonneg(blend_anchor * (1 + guidance.guidance_low / 100))
            gh = _clamp_nonneg(blend_anchor * (1 + guidance.guidance_high / 100))
            g_base = (gl + gh) / 2
            result.base = g_base if result.base is None else (result.base + g_base) / 2
            result.low = gl if result.low is None else min(result.low, gl)
            result.high = gh if result.high is None else max(result.high, gh)

    return result, len(samples), growth_stdev


def _estimate_margin(
    history: list[HistoricalFinancialPoint], value_of, label: str, guidance, assumptions: list[AssumptionItem]
) -> tuple[MetricEstimate, int]:
    values = [value_of(p) for p in history]
    values = [Decimal(str(v)) for v in values if v is not None]

    result = MetricEstimate()
    if len(values) >= MIN_MARGIN_SAMPLES:
        median = Decimal(str(statistics.median(values)))
        stdev = Decimal(str(statistics.stdev(values)))
        result.base = median
        result.low = median - stdev
        result.high = median + stdev
        assumptions.append(
            AssumptionItem(
                text=f"{label} base case: median {median.quantize(Decimal('0.01'))}% over "
                f"{len(values)} historical quarter(s).",
                source="deterministic",
            )
        )
    else:
        assumptions.append(
            AssumptionItem(
                text=f"{label}: insufficient historical data (fewer than 2 quarters) — "
                "no estimate produced.",
                source="deterministic",
            )
        )

    if guidance is not None:
        assumptions.append(
            AssumptionItem(
                text=(
                    f"Guidance available: FY{guidance.fiscal_year} {guidance.metric_label} "
                    f"{guidance.guidance_low}-{guidance.guidance_high} "
                    f"({guidance.period_label or 'unlabeled period'}); blended into the "
                    f"{label} range."
                ),
                source="deterministic",
            )
        )
        gl, gh = guidance.guidance_low, guidance.guidance_high
        g_base = (gl + gh) / 2
        result.base = g_base if result.base is None else (result.base + g_base) / 2
        result.low = gl if result.low is None else min(result.low, gl)
        result.high = gh if result.high is None else max(result.high, gh)

    return result, len(values)


def _derive_pat(
    revenue: MetricEstimate, pat_margin: MetricEstimate, assumptions: list[AssumptionItem]
) -> MetricEstimate:
    result = MetricEstimate()
    if revenue.base is None or pat_margin.base is None:
        assumptions.append(
            AssumptionItem(
                text="PAT: could not be derived — revenue or PAT-margin estimate unavailable.",
                source="deterministic",
            )
        )
        return result
    result.base = _clamp_nonneg(revenue.base * pat_margin.base / 100)
    if revenue.low is not None and pat_margin.low is not None:
        result.low = _clamp_nonneg(revenue.low * pat_margin.low / 100)
    if revenue.high is not None and pat_margin.high is not None:
        result.high = _clamp_nonneg(revenue.high * pat_margin.high / 100)
    assumptions.append(
        AssumptionItem(
            text=f"PAT derived from revenue estimate x historical PAT margin "
            f"({pat_margin.base.quantize(Decimal('0.01'))}%).",
            source="deterministic",
        )
    )
    return result


def _derive_eps(
    pat: MetricEstimate, history: list[HistoricalFinancialPoint], assumptions: list[AssumptionItem]
) -> MetricEstimate:
    result = MetricEstimate()
    share_source = next(
        (p for p in reversed(history) if p.pat and p.eps_diluted and p.pat != 0),
        None,
    )
    if pat.base is None or share_source is None:
        assumptions.append(
            AssumptionItem(
                text="EPS: could not be derived — PAT estimate or an implied diluted share "
                "count from history is unavailable.",
                source="deterministic",
            )
        )
        return result

    implied_shares = share_source.pat / share_source.eps_diluted
    result.base = pat.base / implied_shares
    if pat.low is not None:
        result.low = pat.low / implied_shares
    if pat.high is not None:
        result.high = pat.high / implied_shares
    assumptions.append(
        AssumptionItem(
            text=f"EPS derived from PAT ÷ implied diluted share count "
            f"({implied_shares.quantize(Decimal('0.01'))}, from {share_source.period_end}).",
            source="deterministic",
        )
    )
    return result


def _confidence(
    n_history: int, revenue_samples_n: int, revenue_growth_stdev: Decimal | None, has_guidance: bool
) -> Decimal:
    score = Decimal("0.30")
    score += min(Decimal("0.40"), Decimal("0.10") * n_history)
    if has_guidance:
        score += Decimal("0.15")
    if revenue_samples_n == 0:
        score -= Decimal("0.15")
    if revenue_growth_stdev is not None and revenue_growth_stdev > Decimal("0.15"):
        score -= Decimal("0.10")
    score = max(Decimal("0.05"), min(Decimal("0.95"), score))
    return score.quantize(Decimal("0.0001"))


def _clamp_nonneg(value: Decimal) -> Decimal:
    return max(Decimal("0"), value)
