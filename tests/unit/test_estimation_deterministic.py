from __future__ import annotations

"""Unit tests for services/estimation/deterministic.py — pure function, no
I/O. Exercises the full scenario matrix: rich YoY history, QoQ fallback,
insufficient-data -> None, guidance blending (including the "guidance
rescues a sparse estimate" case), PAT/EPS derivation consistency, and
confidence sensitivity.
"""

from datetime import date
from decimal import Decimal

from investing_agent.schemas.estimation import (
    FeatureSnapshotPayload,
    GuidancePoint,
    HistoricalFinancialPoint,
    TargetPeriodInfo,
)
from investing_agent.services.estimation.deterministic import (
    MIN_GROWTH_SAMPLES,
    estimate_deterministic,
)


def _point(fiscal_year, quarter, period_end, revenue=None, ebitda_margin_pct=None, pat=None, eps_diluted=None):
    return HistoricalFinancialPoint(
        fiscal_year=fiscal_year,
        quarter=quarter,
        period_type="quarter",
        period_end=period_end,
        statement_scope="CONSOLIDATED",
        revenue=Decimal(str(revenue)) if revenue is not None else None,
        ebitda_margin_pct=Decimal(str(ebitda_margin_pct)) if ebitda_margin_pct is not None else None,
        pat=Decimal(str(pat)) if pat is not None else None,
        eps_diluted=Decimal(str(eps_diluted)) if eps_diluted is not None else None,
    )


def _target(fiscal_year=2025, quarter="Q4", period_end=date(2025, 3, 31)):
    return TargetPeriodInfo(
        fiscal_year=fiscal_year, quarter=quarter, period_type="quarter",
        period_end=period_end, label=f"{quarter}FY{str(fiscal_year)[2:]}",
    )


def _snapshot(history, target=None, guidance=None):
    return FeatureSnapshotPayload(
        target_period=target or _target(),
        historical_financials=history,
        guidance=guidance or [],
    )


class TestRichYoYHistory:
    """8 quarters (FY24 Q1-Q4, FY25 Q1-Q3) with a flat 5% YoY revenue
    growth, flat 12% EBITDA margin, and flat 8% PAT margin — every stdev is
    exactly 0, so the whole chain is exactly computable."""

    def _history(self):
        return [
            _point(2024, "Q1", date(2023, 6, 30), revenue=1000, ebitda_margin_pct=12, pat=80),
            _point(2024, "Q2", date(2023, 9, 30), revenue=1020, ebitda_margin_pct=12, pat=81.6),
            _point(2024, "Q3", date(2023, 12, 31), revenue=1040, ebitda_margin_pct=12, pat=83.2),
            _point(2024, "Q4", date(2024, 3, 31), revenue=1060, ebitda_margin_pct=12, pat=84.8),
            _point(2025, "Q1", date(2024, 6, 30), revenue=1050, ebitda_margin_pct=12, pat=84),
            _point(2025, "Q2", date(2024, 9, 30), revenue=1071, ebitda_margin_pct=12, pat=85.68),
            _point(
                2025, "Q3", date(2024, 12, 31), revenue=1092.0, ebitda_margin_pct=12,
                pat=87.36, eps_diluted=5.0,
            ),
        ]

    def test_revenue_uses_yoy_method_anchored_to_prior_year_same_quarter(self) -> None:
        snapshot = _snapshot(self._history())
        estimate = estimate_deterministic(snapshot)

        # anchor = FY24 Q4 revenue (1060), median YoY growth = 5%, stdev = 0
        assert estimate.revenue.base == Decimal("1113.000")
        assert estimate.revenue.low == estimate.revenue.base
        assert estimate.revenue.high == estimate.revenue.base
        assert any("YoY" in a.text for a in estimate.assumptions)

    def test_ebitda_margin_is_flat_median(self) -> None:
        estimate = estimate_deterministic(_snapshot(self._history()))
        assert estimate.ebitda_margin.base == Decimal("12")
        assert estimate.ebitda_margin.low == estimate.ebitda_margin.base
        assert estimate.ebitda_margin.high == estimate.ebitda_margin.base

    def test_pat_derived_from_revenue_times_pat_margin(self) -> None:
        estimate = estimate_deterministic(_snapshot(self._history()))
        # PAT margin is flat 8% across all history -> pat.base = revenue.base * 0.08
        expected_pat = (estimate.revenue.base * Decimal("8") / 100)
        assert estimate.pat.base == expected_pat
        assert any("PAT derived from revenue" in a.text for a in estimate.assumptions)

    def test_eps_derived_from_pat_over_implied_shares(self) -> None:
        estimate = estimate_deterministic(_snapshot(self._history()))
        # implied_shares = 87.36 / 5.0 = 17.472 (from the most recent point, FY25 Q3)
        implied_shares = Decimal("87.36") / Decimal("5.0")
        expected_eps = estimate.pat.base / implied_shares
        assert estimate.eps.base == expected_eps
        assert any("implied diluted share count" in a.text for a in estimate.assumptions)

    def test_confidence_reflects_dense_history_and_no_guidance(self) -> None:
        estimate = estimate_deterministic(_snapshot(self._history()))
        # 7 history quarters (capped at +0.40), no guidance, samples present, low stdev
        assert estimate.confidence == Decimal("0.7000")


class TestQoQFallback:
    """Only 3 quarters, all in the same fiscal year as each other — no
    same-quarter-prior-year point exists, so YoY is impossible and it must
    fall back to QoQ."""

    def _history(self):
        return [
            _point(2025, "Q1", date(2024, 6, 30), revenue=1000),
            _point(2025, "Q2", date(2024, 9, 30), revenue=1050),
            _point(2025, "Q3", date(2024, 12, 31), revenue=1100),
        ]

    def test_falls_back_to_qoq_when_no_yoy_point_exists(self) -> None:
        estimate = estimate_deterministic(_snapshot(self._history()))
        assert estimate.revenue.base is not None
        assert any("QoQ" in a.text for a in estimate.assumptions)
        assert not any("YoY" in a.text for a in estimate.assumptions)


class TestInsufficientData:
    def test_zero_history_produces_all_none_metrics(self) -> None:
        estimate = estimate_deterministic(_snapshot([]))
        assert estimate.revenue == estimate.revenue.__class__()
        assert estimate.ebitda_margin.base is None
        assert estimate.pat.base is None
        assert estimate.eps.base is None
        assert estimate.confidence == Decimal("0.1500")

    def test_single_quarter_is_insufficient_for_growth_samples(self) -> None:
        history = [_point(2025, "Q3", date(2024, 12, 31), revenue=1000, ebitda_margin_pct=12, pat=80)]
        estimate = estimate_deterministic(_snapshot(history))
        assert estimate.revenue.base is None
        assert any("insufficient historical data" in a.text for a in estimate.assumptions)

    def test_below_min_growth_samples_threshold_still_produces_none(self) -> None:
        # Exactly MIN_GROWTH_SAMPLES - 1 usable QoQ pairs.
        history = [_point(2025, "Q1", date(2024, 6, 30), revenue=1000)]
        assert MIN_GROWTH_SAMPLES == 2
        estimate = estimate_deterministic(_snapshot(history))
        assert estimate.revenue.base is None


class TestGuidanceBlending:
    def test_guidance_widens_band_and_pulls_base_toward_midpoint(self) -> None:
        history = [
            _point(2024, "Q4", date(2024, 3, 31), revenue=1000),
            _point(2025, "Q1", date(2024, 6, 30), revenue=1010),
            _point(2025, "Q2", date(2024, 9, 30), revenue=1020),
        ]
        target = _target(fiscal_year=2025, quarter="Q3", period_end=date(2024, 12, 31))
        guidance = [
            GuidancePoint(
                fiscal_year=2025, guidance_type="revenue", metric_label="Revenue growth",
                guidance_value_text="12-15%", guidance_low=Decimal("12"), guidance_high=Decimal("15"),
                period_label="FY25 full year",
            )
        ]
        estimate = estimate_deterministic(_snapshot(history, target=target, guidance=guidance))
        assert any("Guidance available" in a.text and "blended into" in a.text for a in estimate.assumptions)
        assert estimate.revenue.base is not None

    def test_guidance_rescues_estimate_when_history_alone_is_insufficient(self) -> None:
        # Only 1 historical quarter -> growth-stat method fails outright,
        # but guidance should still produce a usable range anchored to it.
        history = [_point(2025, "Q2", date(2024, 9, 30), revenue=1000)]
        target = _target(fiscal_year=2025, quarter="Q3", period_end=date(2024, 12, 31))
        guidance = [
            GuidancePoint(
                fiscal_year=2025, guidance_type="revenue", metric_label="Revenue growth",
                guidance_value_text="12-15%", guidance_low=Decimal("12"), guidance_high=Decimal("15"),
                period_label="FY25 full year",
            )
        ]
        estimate = estimate_deterministic(_snapshot(history, target=target, guidance=guidance))
        assert estimate.revenue.low == Decimal("1120.00")
        assert estimate.revenue.base == Decimal("1135.00")
        assert estimate.revenue.high == Decimal("1150.00")

    def test_guidance_for_wrong_fiscal_year_is_ignored(self) -> None:
        history = [_point(2025, "Q2", date(2024, 9, 30), revenue=1000)]
        target = _target(fiscal_year=2025, quarter="Q3", period_end=date(2024, 12, 31))
        guidance = [
            GuidancePoint(
                fiscal_year=2026, guidance_type="revenue", metric_label="Revenue growth",
                guidance_value_text="12-15%", guidance_low=Decimal("12"), guidance_high=Decimal("15"),
            )
        ]
        estimate = estimate_deterministic(_snapshot(history, target=target, guidance=guidance))
        assert not any("Guidance available" in a.text for a in estimate.assumptions)
        assert estimate.revenue.base is None


class TestConfidenceBounds:
    def test_confidence_never_below_floor_or_above_ceiling(self) -> None:
        empty = estimate_deterministic(_snapshot([]))
        assert Decimal("0.05") <= empty.confidence <= Decimal("0.95")

        history = [
            _point(2024, q, date(2023, month, day), revenue=1000 + i * 10, ebitda_margin_pct=12, pat=80)
            for i, (q, month, day) in enumerate(
                [("Q1", 6, 30), ("Q2", 9, 30), ("Q3", 12, 31), ("Q4", 3, 31)]
            )
        ]
        rich = estimate_deterministic(_snapshot(history))
        assert Decimal("0.05") <= rich.confidence <= Decimal("0.95")
