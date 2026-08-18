from __future__ import annotations

"""score_estimate: pure function scoring one EstimateRun against the actual
FinancialResult for its target period.

No I/O, no LLM — every field is arithmetic over already-persisted Decimal
values, mirroring how services/estimation/deterministic.py::estimate_deterministic
is a pure function over a FeatureSnapshotPayload. Consistent with the design
rule of this codebase: Python calculates, nothing here is invented.

A metric's error/within-band fields are None whenever the corresponding
EstimateRun metric or actual value is None — never coerced to 0 or guessed,
same never-guess discipline as the estimator itself.
"""

from decimal import Decimal

from investing_agent.schemas.backtesting import BacktestScoreCreate
from investing_agent.schemas.estimation import EstimateRunRead
from investing_agent.schemas.financials import FinancialResultRead

_INLINE_BAND = Decimal("0.03")  # +/-3% around the base case counts as "inline"
_ERROR_QUANT = Decimal("0.0001")
_BPS_QUANT = Decimal("0.01")
_CONFIDENCE_LOW = Decimal("0.40")
_CONFIDENCE_HIGH = Decimal("0.70")


def _error_pct(estimate: Decimal | None, actual: Decimal | None) -> Decimal | None:
    if estimate is None or actual is None or estimate == 0:
        return None
    return (((actual - estimate) / abs(estimate)) * 100).quantize(_ERROR_QUANT)


def _margin_error_bps(estimate_pct: Decimal | None, actual_pct: Decimal | None) -> Decimal | None:
    if estimate_pct is None or actual_pct is None:
        return None
    return ((actual_pct - estimate_pct) * 100).quantize(_BPS_QUANT)


def _within_band(low: Decimal | None, high: Decimal | None, actual: Decimal | None) -> bool | None:
    if low is None or high is None or actual is None:
        return None
    return low <= actual <= high


def _surprise_direction(pat_base: Decimal | None, actual_pat: Decimal | None) -> str | None:
    if pat_base is None or actual_pat is None or pat_base == 0:
        return None
    threshold = abs(pat_base) * _INLINE_BAND
    if actual_pat > pat_base + threshold:
        return "beat"
    if actual_pat < pat_base - threshold:
        return "miss"
    return "inline"


def _growth_direction_correct(
    pat_base: Decimal | None, actual_pat: Decimal | None, baseline_pat: Decimal | None
) -> bool | None:
    """Did the estimate's base case correctly call growth vs decline in PAT
    relative to the most recent historical comparable, independent of
    magnitude? None when either side is exactly flat vs baseline (no clear
    direction to be right or wrong about)."""
    if pat_base is None or actual_pat is None or baseline_pat is None:
        return None
    predicted_delta = pat_base - baseline_pat
    actual_delta = actual_pat - baseline_pat
    if predicted_delta == 0 or actual_delta == 0:
        return None
    return (predicted_delta > 0) == (actual_delta > 0)


def _confidence_bucket(confidence: Decimal | None) -> str | None:
    if confidence is None:
        return None
    if confidence < _CONFIDENCE_LOW:
        return "low"
    if confidence < _CONFIDENCE_HIGH:
        return "medium"
    return "high"


def score_estimate(
    *,
    run: EstimateRunRead,
    actual: FinancialResultRead,
    baseline_pat: Decimal | None = None,
) -> BacktestScoreCreate:
    return BacktestScoreCreate(
        company_id=run.company_id,
        financial_period_id=run.financial_period_id,
        estimate_run_id=run.id,
        financial_result_id=actual.id,
        cutoff_at=run.cutoff_at,
        model_version=run.model_version,
        revenue_error_pct=_error_pct(run.revenue_base, actual.revenue),
        pat_error_pct=_error_pct(run.pat_base, actual.pat),
        eps_error_pct=_error_pct(run.eps_base, actual.eps_diluted),
        margin_error_bps=_margin_error_bps(run.ebitda_margin_base, actual.ebitda_margin_pct),
        within_band_revenue=_within_band(run.revenue_low, run.revenue_high, actual.revenue),
        within_band_pat=_within_band(run.pat_low, run.pat_high, actual.pat),
        within_band_eps=_within_band(run.eps_low, run.eps_high, actual.eps_diluted),
        surprise_direction=_surprise_direction(run.pat_base, actual.pat),
        growth_direction_correct=_growth_direction_correct(run.pat_base, actual.pat, baseline_pat),
        confidence_bucket=_confidence_bucket(run.confidence),
    )
