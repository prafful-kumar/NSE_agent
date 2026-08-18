from __future__ import annotations

"""Unit tests for services/backtesting/report.py::summarize/group_and_summarize
— pure aggregation over BacktestScoreRead rows, no I/O."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from investing_agent.schemas.backtesting import BacktestScoreRead
from investing_agent.services.backtesting.report import group_and_summarize, summarize

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _score(**overrides) -> BacktestScoreRead:
    defaults = dict(
        id=uuid.uuid4(), company_id=uuid.uuid4(), financial_period_id=uuid.uuid4(),
        estimate_run_id=uuid.uuid4(), financial_result_id=uuid.uuid4(), cutoff_at=NOW,
        model_version="deterministic-v1",
        revenue_error_pct=None, pat_error_pct=None, eps_error_pct=None, margin_error_bps=None,
        within_band_revenue=None, within_band_pat=None, within_band_eps=None,
        surprise_direction=None, growth_direction_correct=None, confidence_bucket=None,
        created_at=NOW, updated_at=NOW,
    )
    defaults.update(overrides)
    return BacktestScoreRead(**defaults)


class TestSummarizeEmptyInput:
    def test_no_scores_gives_all_none(self) -> None:
        summary = summarize([])
        assert summary["n"] == 0
        assert summary["revenue_mape_pct"] is None
        assert summary["revenue_mape_n"] == 0
        assert summary["surprise_direction_distribution_pct"] == {}


class TestMapeIsMeanAbsoluteError:
    def test_averages_absolute_errors_not_signed(self) -> None:
        scores = [
            _score(pat_error_pct=Decimal("10")),
            _score(pat_error_pct=Decimal("-20")),
        ]
        summary = summarize(scores)
        # mean(|10|, |-20|) = 15, not mean(10, -20) = -5
        assert summary["pat_mape_pct"] == Decimal("15")
        assert summary["pat_mape_n"] == 2

    def test_none_values_excluded_from_mean_and_count(self) -> None:
        scores = [_score(pat_error_pct=Decimal("10")), _score(pat_error_pct=None)]
        summary = summarize(scores)
        assert summary["pat_mape_pct"] == Decimal("10")
        assert summary["pat_mape_n"] == 1


class TestWithinBandHitRate:
    def test_hit_rate_is_percentage_of_true(self) -> None:
        scores = [
            _score(within_band_pat=True),
            _score(within_band_pat=True),
            _score(within_band_pat=False),
            _score(within_band_pat=None),
        ]
        summary = summarize(scores)
        assert summary["pat_within_band_pct"] == Decimal("200") / Decimal("3")
        assert summary["pat_within_band_n"] == 3


class TestGrowthDirectionAccuracy:
    def test_accuracy_is_percentage_of_correct(self) -> None:
        scores = [
            _score(growth_direction_correct=True),
            _score(growth_direction_correct=False),
        ]
        summary = summarize(scores)
        assert summary["growth_direction_accuracy_pct"] == Decimal("50")
        assert summary["growth_direction_n"] == 2


class TestSurpriseDirectionDistribution:
    def test_distribution_sums_to_one_hundred_percent(self) -> None:
        scores = [
            _score(surprise_direction="beat"),
            _score(surprise_direction="beat"),
            _score(surprise_direction="inline"),
            _score(surprise_direction="miss"),
        ]
        summary = summarize(scores)
        dist = summary["surprise_direction_distribution_pct"]
        assert dist["beat"] == Decimal("50")
        assert dist["inline"] == Decimal("25")
        assert dist["miss"] == Decimal("25")


class TestGroupAndSummarize:
    def test_groups_by_key_fn_and_summarizes_each_bucket(self) -> None:
        scores = [
            _score(model_version="deterministic-v1", pat_error_pct=Decimal("10")),
            _score(model_version="deterministic-v1", pat_error_pct=Decimal("20")),
            _score(model_version="deterministic-v2", pat_error_pct=Decimal("5")),
        ]
        grouped = group_and_summarize(scores, lambda s: s.model_version)
        assert grouped["deterministic-v1"]["n"] == 2
        assert grouped["deterministic-v1"]["pat_mape_pct"] == Decimal("15")
        assert grouped["deterministic-v2"]["n"] == 1
        assert grouped["deterministic-v2"]["pat_mape_pct"] == Decimal("5")
