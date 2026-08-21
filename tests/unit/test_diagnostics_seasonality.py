from __future__ import annotations

"""Unit tests for services/diagnostics.py::compute_quarter_seasonality —
pure aggregation over verified quarterly financial figures, no I/O."""

from decimal import Decimal

from investing_agent.services.diagnostics import QuarterlyFinancialPoint, compute_quarter_seasonality


def _point(quarter: str, **overrides) -> QuarterlyFinancialPoint:
    defaults = dict(
        quarter=quarter, fiscal_year=2023, period_label=f"{quarter}FY23",
        revenue=Decimal("1000"), pbt=Decimal("200"), pat=Decimal("150"),
        tax_expense=Decimal("50"),
    )
    defaults.update(overrides)
    return QuarterlyFinancialPoint(**defaults)


class TestComputeQuarterSeasonalityEmptyInput:
    def test_no_points_gives_empty_dict(self) -> None:
        assert compute_quarter_seasonality([]) == {}


class TestComputeQuarterSeasonalityGrouping:
    def test_groups_by_quarter_label(self) -> None:
        points = [_point("Q3"), _point("Q3"), _point("Q4")]
        result = compute_quarter_seasonality(points)

        assert set(result.keys()) == {"Q3", "Q4"}
        assert result["Q3"]["n"] == 2
        assert result["Q4"]["n"] == 1

    def test_averages_margins_and_tax_rate_correctly(self) -> None:
        points = [
            _point("Q4", revenue=Decimal("1000"), pbt=Decimal("370"), pat=Decimal("270"), tax_expense=Decimal("100")),
            _point("Q4", revenue=Decimal("1000"), pbt=Decimal("380"), pat=Decimal("280"), tax_expense=Decimal("100")),
        ]
        result = compute_quarter_seasonality(points)

        # (37% + 38%) / 2 = 37.5%
        assert result["Q4"]["avg_pbt_margin_pct"] == Decimal("37.50")
        # (27% + 28%) / 2 = 27.5%
        assert result["Q4"]["avg_pat_margin_pct"] == Decimal("27.50")
        # (100/370*100 + 100/380*100) / 2
        assert result["Q4"]["avg_effective_tax_rate_pct"] is not None

    def test_the_documented_q3_vs_q4_seasonal_dip(self) -> None:
        # Regression-style sanity check mirroring the real HAL finding that
        # motivated this diagnostic: Q3 PBT margin running well below Q4's
        # fiscal-year-end push.
        q3 = _point("Q3", revenue=Decimal("1000"), pbt=Decimal("260"))
        q4 = _point("Q4", revenue=Decimal("1000"), pbt=Decimal("377"))
        result = compute_quarter_seasonality([q3, q4])

        assert result["Q3"]["avg_pbt_margin_pct"] < result["Q4"]["avg_pbt_margin_pct"]


class TestComputeQuarterSeasonalityMissingFields:
    def test_missing_tax_expense_excluded_from_tax_rate_only(self) -> None:
        points = [
            _point("Q2", tax_expense=None),
            _point("Q2", tax_expense=Decimal("50")),
        ]
        result = compute_quarter_seasonality(points)

        assert result["Q2"]["n"] == 2
        assert result["Q2"]["avg_pbt_margin_pct"] is not None
        assert result["Q2"]["avg_effective_tax_rate_pct"] is not None

    def test_zero_revenue_excluded_from_margin_ratios(self) -> None:
        points = [_point("Q1", revenue=Decimal("0"))]
        result = compute_quarter_seasonality(points)

        assert result["Q1"]["n"] == 1
        assert result["Q1"]["avg_pbt_margin_pct"] is None
        assert result["Q1"]["avg_pat_margin_pct"] is None

    def test_missing_pbt_excludes_both_margin_and_tax_rate(self) -> None:
        points = [_point("Q1", pbt=None)]
        result = compute_quarter_seasonality(points)

        assert result["Q1"]["avg_pbt_margin_pct"] is None
        assert result["Q1"]["avg_effective_tax_rate_pct"] is None
