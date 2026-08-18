from __future__ import annotations

"""Unit tests for services/backtesting/scoring.py::score_estimate — pure
arithmetic over an EstimateRunRead + the actual FinancialResultRead, no I/O.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from investing_agent.schemas.estimation import EstimateRunRead
from investing_agent.schemas.financials import FinancialResultRead
from investing_agent.services.backtesting.scoring import score_estimate

COMPANY_ID = uuid.uuid4()
PERIOD_ID = uuid.uuid4()
SNAPSHOT_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()
RESULT_ID = uuid.uuid4()
CUTOFF = datetime(2025, 12, 30, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _run(**overrides) -> EstimateRunRead:
    defaults = dict(
        id=RUN_ID, company_id=COMPANY_ID, financial_period_id=PERIOD_ID, cutoff_at=CUTOFF,
        model_version="deterministic-v1", feature_snapshot_id=SNAPSHOT_ID,
        revenue_low=Decimal("1000"), revenue_base=Decimal("1100"), revenue_high=Decimal("1200"),
        ebitda_margin_low=Decimal("11"), ebitda_margin_base=Decimal("12"), ebitda_margin_high=Decimal("13"),
        pat_low=Decimal("80"), pat_base=Decimal("90"), pat_high=Decimal("100"),
        eps_low=Decimal("4.5"), eps_base=Decimal("5.0"), eps_high=Decimal("5.5"),
        confidence=Decimal("0.6"), assumptions=[],
        created_at=NOW, updated_at=NOW,
    )
    defaults.update(overrides)
    return EstimateRunRead(**defaults)


def _actual(**overrides) -> FinancialResultRead:
    defaults = dict(
        id=RESULT_ID, period_id=PERIOD_ID, company_id=COMPANY_ID, symbol="BEL",
        statement_scope="CONSOLIDATED", reporting_basis="QUARTER", is_audited=True,
        result_date=date(2025, 12, 31), currency="INR", unit_scale="CRORE",
        revenue=Decimal("1100"), ebitda=None, ebitda_margin_pct=Decimal("12"), ebitda_source=None,
        pbt=None, pat=Decimal("90"), pat_margin_pct=None, eps_basic=None, eps_diluted=Decimal("5.0"),
        total_debt=None, cash_equivalents=None, operating_cash_flow=None, roe_pct=None, roce_pct=None,
        version=1, is_latest=True, source_type="test", source_url=None,
        published_at=NOW, available_at=NOW, data_category="fact",
        verification_status="unverified", verification_method=None, verified_at=None,
        created_at=NOW, updated_at=NOW,
    )
    defaults.update(overrides)
    return FinancialResultRead(**defaults)


class TestErrorPct:
    def test_exact_match_gives_zero_error(self) -> None:
        score = score_estimate(run=_run(), actual=_actual())
        assert score.revenue_error_pct == Decimal("0.0000")
        assert score.pat_error_pct == Decimal("0.0000")
        assert score.eps_error_pct == Decimal("0.0000")

    def test_actual_above_base_gives_positive_error(self) -> None:
        score = score_estimate(run=_run(revenue_base=Decimal("1000")), actual=_actual(revenue=Decimal("1100")))
        assert score.revenue_error_pct == Decimal("10.0000")

    def test_actual_below_base_gives_negative_error(self) -> None:
        score = score_estimate(run=_run(pat_base=Decimal("100")), actual=_actual(pat=Decimal("90")))
        assert score.pat_error_pct == Decimal("-10.0000")

    def test_none_base_gives_none_error(self) -> None:
        score = score_estimate(
            run=_run(revenue_low=None, revenue_base=None, revenue_high=None), actual=_actual()
        )
        assert score.revenue_error_pct is None

    def test_none_actual_gives_none_error(self) -> None:
        score = score_estimate(run=_run(), actual=_actual(pat=None))
        assert score.pat_error_pct is None


class TestMarginErrorBps:
    def test_margin_beat_by_one_point_is_100bps(self) -> None:
        score = score_estimate(
            run=_run(ebitda_margin_base=Decimal("12")), actual=_actual(ebitda_margin_pct=Decimal("13"))
        )
        assert score.margin_error_bps == Decimal("100.00")

    def test_none_either_side_gives_none(self) -> None:
        score = score_estimate(
            run=_run(ebitda_margin_low=None, ebitda_margin_base=None, ebitda_margin_high=None),
            actual=_actual(),
        )
        assert score.margin_error_bps is None


class TestWithinBand:
    def test_actual_inside_band_is_true(self) -> None:
        score = score_estimate(run=_run(), actual=_actual(revenue=Decimal("1150")))
        assert score.within_band_revenue is True

    def test_actual_outside_band_is_false(self) -> None:
        score = score_estimate(run=_run(), actual=_actual(revenue=Decimal("50")))
        assert score.within_band_revenue is False

    def test_actual_on_boundary_is_true(self) -> None:
        score = score_estimate(run=_run(), actual=_actual(revenue=Decimal("1200")))
        assert score.within_band_revenue is True

    def test_none_band_gives_none(self) -> None:
        score = score_estimate(
            run=_run(revenue_low=None, revenue_base=None, revenue_high=None), actual=_actual()
        )
        assert score.within_band_revenue is None


class TestSurpriseDirection:
    def test_actual_well_above_base_is_beat(self) -> None:
        score = score_estimate(run=_run(pat_base=Decimal("100")), actual=_actual(pat=Decimal("110")))
        assert score.surprise_direction == "beat"

    def test_actual_well_below_base_is_miss(self) -> None:
        score = score_estimate(run=_run(pat_base=Decimal("100")), actual=_actual(pat=Decimal("90")))
        assert score.surprise_direction == "miss"

    def test_actual_within_three_percent_is_inline(self) -> None:
        score = score_estimate(run=_run(pat_base=Decimal("100")), actual=_actual(pat=Decimal("102")))
        assert score.surprise_direction == "inline"

    def test_boundary_at_exactly_three_percent_is_inline(self) -> None:
        score = score_estimate(run=_run(pat_base=Decimal("100")), actual=_actual(pat=Decimal("103")))
        assert score.surprise_direction == "inline"

    def test_none_pat_base_gives_none(self) -> None:
        score = score_estimate(
            run=_run(pat_low=None, pat_base=None, pat_high=None), actual=_actual()
        )
        assert score.surprise_direction is None


class TestGrowthDirectionCorrect:
    def test_both_predict_and_actual_grow_is_correct(self) -> None:
        score = score_estimate(
            run=_run(pat_base=Decimal("100")), actual=_actual(pat=Decimal("95")),
            baseline_pat=Decimal("80"),
        )
        # predicted delta = 100-80=+20 (growth), actual delta = 95-80=+15 (growth) -> correct
        assert score.growth_direction_correct is True

    def test_predicted_growth_but_actual_decline_is_incorrect(self) -> None:
        score = score_estimate(
            run=_run(pat_base=Decimal("100")), actual=_actual(pat=Decimal("70")),
            baseline_pat=Decimal("80"),
        )
        assert score.growth_direction_correct is False

    def test_no_baseline_gives_none(self) -> None:
        score = score_estimate(run=_run(), actual=_actual(), baseline_pat=None)
        assert score.growth_direction_correct is None

    def test_flat_predicted_delta_gives_none(self) -> None:
        score = score_estimate(
            run=_run(pat_base=Decimal("80")), actual=_actual(pat=Decimal("90")),
            baseline_pat=Decimal("80"),
        )
        assert score.growth_direction_correct is None


class TestConfidenceBucket:
    def test_low_confidence(self) -> None:
        score = score_estimate(run=_run(confidence=Decimal("0.2")), actual=_actual())
        assert score.confidence_bucket == "low"

    def test_medium_confidence(self) -> None:
        score = score_estimate(run=_run(confidence=Decimal("0.5")), actual=_actual())
        assert score.confidence_bucket == "medium"

    def test_high_confidence(self) -> None:
        score = score_estimate(run=_run(confidence=Decimal("0.85")), actual=_actual())
        assert score.confidence_bucket == "high"

    def test_boundary_at_seventy_percent_is_high(self) -> None:
        score = score_estimate(run=_run(confidence=Decimal("0.70")), actual=_actual())
        assert score.confidence_bucket == "high"

    def test_none_confidence_gives_none(self) -> None:
        score = score_estimate(run=_run(confidence=None), actual=_actual())
        assert score.confidence_bucket is None


class TestIdentityFieldsCarryThrough:
    def test_identity_fields_come_from_run_and_actual(self) -> None:
        score = score_estimate(run=_run(), actual=_actual())
        assert score.company_id == COMPANY_ID
        assert score.financial_period_id == PERIOD_ID
        assert score.estimate_run_id == RUN_ID
        assert score.financial_result_id == RESULT_ID
        assert score.cutoff_at == CUTOFF
        assert score.model_version == "deterministic-v1"
