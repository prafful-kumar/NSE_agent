from __future__ import annotations

"""Unit tests for services/walkforward/aggregation.py's pure grouping/metric
functions (no DB access -- AuditRow instances built directly)."""

import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal

from investing_agent.services.walkforward.aggregation import (
    aggregate_by,
    build_report,
    concentration_bucket,
    data_quality_counts,
    decision_dollar_impact,
    drawdown_distribution,
    group_by,
    holding_age_bucket,
    summarize_horizon,
)
from investing_agent.services.walkforward.audit import AuditRow


def _row(
    symbol="BEL",
    decision_at=date(2021, 6, 15),
    action="BUY",
    data_quality_status="CLEAN",
    outcome_status="SCORED",
    holding_age_days=0,
    concentration_pct=Decimal("0.10"),
    quantity_held=Decimal("10"),
    hold_quantity_held=Decimal("0"),
    entry_price=Decimal("100"),
    stock_1m=None,
    excess_1m=None,
    hold_stock_1m=None,
    max_drawdown_pct=None,
    included=True,
    exclusion_reason=None,
) -> AuditRow:
    horizons = ("1m", "3m", "6m", "12m")
    stock = dict.fromkeys(horizons)
    excess = dict.fromkeys(horizons)
    hold_stock = dict.fromkeys(horizons)
    stock["1m"] = stock_1m
    excess["1m"] = excess_1m
    hold_stock["1m"] = hold_stock_1m
    return AuditRow(
        symbol=symbol,
        decision_at=decision_at,
        decision_id=uuid.uuid4(),
        hold_decision_id=uuid.uuid4(),
        action=action,
        data_quality_status=data_quality_status,
        reconstruction_warnings=[],
        outcome_status=outcome_status,
        quantity_held=quantity_held,
        invested_capital=Decimal("1000"),
        hold_quantity_held=hold_quantity_held,
        holding_age_days=holding_age_days,
        concentration_pct=concentration_pct,
        calendar_year=decision_at.year,
        entry_price=entry_price,
        stock_return=stock,
        benchmark_return=dict.fromkeys(horizons),
        excess_return=excess,
        hold_stock_return=hold_stock,
        max_drawdown_pct=max_drawdown_pct,
        data_quality_notes=[],
        included_in_aggregate=included,
        exclusion_reason=exclusion_reason,
        trade_evidence=[],
    )


class TestHoldingAgeBucket:
    def test_none_is_unknown(self):
        assert holding_age_bucket(None) == "UNKNOWN"

    def test_boundaries(self):
        assert holding_age_bucket(0) == "<3M"
        assert holding_age_bucket(89) == "<3M"
        assert holding_age_bucket(90) == "3-6M"
        assert holding_age_bucket(181) == "3-6M"
        assert holding_age_bucket(182) == "6-12M"
        assert holding_age_bucket(364) == "6-12M"
        assert holding_age_bucket(365) == "12-24M"
        assert holding_age_bucket(729) == "12-24M"
        assert holding_age_bucket(730) == "24M+"


class TestConcentrationBucket:
    def test_none_is_unknown(self):
        assert concentration_bucket(None) == "UNKNOWN"

    def test_boundaries(self):
        assert concentration_bucket(Decimal("0.05")) == "<10%"
        assert concentration_bucket(Decimal("0.10")) == "10-25%"
        assert concentration_bucket(Decimal("0.25")) == "25-50%"
        assert concentration_bucket(Decimal("0.50")) == "50%+"
        assert concentration_bucket(Decimal("0.99")) == "50%+"


class TestSummarizeHorizon:
    def test_excludes_non_included_rows_entirely(self):
        rows = [
            _row(stock_1m=Decimal("0.10"), excess_1m=Decimal("0.05"), included=True),
            _row(stock_1m=Decimal("0.90"), excess_1m=Decimal("0.80"), included=False, exclusion_reason="RECONSTRUCTION_WARNING"),
        ]
        summary = summarize_horizon(rows, "1m")
        assert summary["n_included"] == 1
        assert summary["n_excluded"] == 1
        assert summary["n_scored_this_horizon"] == 1
        assert summary["median_stock_return_pct"] == Decimal("10")

    def test_median_and_mean_and_rates(self):
        rows = [
            _row(stock_1m=Decimal("0.10"), excess_1m=Decimal("0.05")),
            _row(stock_1m=Decimal("-0.10"), excess_1m=Decimal("-0.20")),
            _row(stock_1m=Decimal("0.20"), excess_1m=Decimal("0.15")),
        ]
        summary = summarize_horizon(rows, "1m")
        assert summary["n_scored_this_horizon"] == 3
        assert summary["median_stock_return_pct"] == Decimal("10")
        assert summary["mean_stock_return_pct"] == Decimal("20") / Decimal(3)
        assert summary["positive_return_rate_pct"] == Decimal("200") / Decimal(3)
        assert summary["benchmark_outperform_rate_pct"] == Decimal("200") / Decimal(3)

    def test_horizon_with_no_scored_rows_is_all_none(self):
        rows = [_row(stock_1m=None, excess_1m=None)]
        summary = summarize_horizon(rows, "1m")
        assert summary["n_scored_this_horizon"] == 0
        assert summary["median_stock_return_pct"] is None
        assert summary["positive_return_rate_pct"] is None

    def test_tri_comparison_uses_tri_excess_not_price_index_excess(self):
        row = _row(stock_1m=Decimal("0.10"), excess_1m=Decimal("0.05"))
        tri_row = replace(row, excess_return_tri={"1m": Decimal("-0.02")})
        summary = summarize_horizon([tri_row], "1m", "TRI")
        assert summary["median_excess_return_pct"] == Decimal("-2")
        assert summary["benchmark_outperform_rate_pct"] == Decimal("0")

    def test_decision_dollar_impact_uses_quantity_delta_not_return_delta(self):
        # A BUY: HOLD_BASELINE quantity is 0 (no prior position), ACTUAL
        # bought 10 shares. score_outcome gives both sources the identical
        # 10% stock_return_1m (it never looks at quantity), so diffing
        # returns would wrongly read as zero impact -- the real impact is
        # the 10 incremental shares capturing that 10% move.
        rows = [_row(quantity_held=Decimal("10"), hold_quantity_held=Decimal("0"), entry_price=Decimal("100"), stock_1m=Decimal("0.10"))]
        summary = summarize_horizon(rows, "1m")
        # quantity_delta=10, price_move_per_share=100*0.10=10 -> impact=100
        assert summary["decision_dollar_impact_median"] == Decimal("100")
        assert summary["decision_dollar_impact_n"] == 1
        assert summary["decision_dollar_impact_positive_rate_pct"] == Decimal("100")

    def test_decision_dollar_impact_none_when_hold_quantity_missing(self):
        rows = [_row(hold_quantity_held=None, stock_1m=Decimal("0.10"))]
        summary = summarize_horizon(rows, "1m")
        assert summary["decision_dollar_impact_n"] == 0
        assert summary["decision_dollar_impact_median"] is None


class TestDecisionDollarImpact:
    def test_exit_has_negative_quantity_delta(self):
        # EXIT: ACTUAL quantity is 0, HOLD_BASELINE kept the prior 10 shares
        # -- i.e. by exiting, the decision gave up exposure to a rise.
        row = _row(quantity_held=Decimal("0"), hold_quantity_held=Decimal("10"), entry_price=Decimal("100"), stock_1m=Decimal("0.10"))
        impact = decision_dollar_impact(row, "1m")
        assert impact == Decimal("-100")

    def test_no_quantity_change_is_zero_impact(self):
        row = _row(quantity_held=Decimal("10"), hold_quantity_held=Decimal("10"), stock_1m=Decimal("0.10"))
        assert decision_dollar_impact(row, "1m") == Decimal("0")

    def test_missing_stock_return_is_none(self):
        row = _row(quantity_held=Decimal("10"), hold_quantity_held=Decimal("0"), stock_1m=None)
        assert decision_dollar_impact(row, "1m") is None

    def test_a_buy_that_rose_but_underperformed_benchmark_is_visible_separately(self):
        # Explicit requirement: absolute positive return must not be conflated
        # with benchmark outperformance.
        rows = [_row(stock_1m=Decimal("0.10"), excess_1m=Decimal("-0.20"))]
        summary = summarize_horizon(rows, "1m")
        assert summary["median_stock_return_pct"] == Decimal("10")
        assert summary["median_excess_return_pct"] == Decimal("-20")
        assert summary["positive_return_rate_pct"] == Decimal("100")
        assert summary["benchmark_outperform_rate_pct"] == Decimal("0")


class TestGroupByAndAggregateBy:
    def test_group_by_action(self):
        rows = [_row(action="BUY"), _row(action="BUY"), _row(action="REDUCE")]
        groups = group_by(rows, lambda r: r.action)
        assert len(groups["BUY"]) == 2
        assert len(groups["REDUCE"]) == 1

    def test_aggregate_by_returns_per_group_summary(self):
        rows = [
            _row(action="BUY", stock_1m=Decimal("0.10")),
            _row(action="REDUCE", stock_1m=Decimal("-0.05")),
        ]
        result = aggregate_by(rows, lambda r: r.action, "1m")
        assert result["BUY"]["median_stock_return_pct"] == Decimal("10")
        assert result["REDUCE"]["median_stock_return_pct"] == Decimal("-5")


class TestDrawdownDistribution:
    def test_empty_is_all_none(self):
        result = drawdown_distribution([])
        assert result == {"n": 0, "median_pct": None, "mean_pct": None, "worst_pct": None, "best_pct": None}

    def test_excludes_rows_without_drawdown_or_not_included(self):
        rows = [
            _row(max_drawdown_pct=Decimal("-0.30")),
            _row(max_drawdown_pct=None),
            _row(max_drawdown_pct=Decimal("-0.10"), included=False, exclusion_reason="WASH_TRADE_HOLD"),
        ]
        result = drawdown_distribution(rows)
        assert result["n"] == 1
        assert result["median_pct"] == Decimal("-30")
        assert result["worst_pct"] == Decimal("-30")


class TestDataQualityCounts:
    def test_counts_by_status_and_exclusion_reason(self):
        rows = [
            _row(data_quality_status="CLEAN", outcome_status="SCORED", included=True, exclusion_reason=None),
            _row(data_quality_status="RECONSTRUCTION_WARNING", outcome_status="SCORED", included=False, exclusion_reason="RECONSTRUCTION_WARNING"),
        ]
        counts = data_quality_counts(rows)
        assert counts["data_quality:CLEAN"] == 1
        assert counts["data_quality:RECONSTRUCTION_WARNING"] == 1
        assert counts["exclusion:INCLUDED"] == 1
        assert counts["exclusion:RECONSTRUCTION_WARNING"] == 1


class TestBuildReport:
    def test_shape_covers_all_required_breakdowns(self):
        rows = [
            _row(action="BUY", decision_at=date(2020, 6, 15), holding_age_days=0, concentration_pct=Decimal("0.05"), stock_1m=Decimal("0.10")),
            _row(action="ADD", decision_at=date(2021, 8, 31), holding_age_days=400, concentration_pct=Decimal("0.30"), stock_1m=Decimal("0.05")),
        ]
        report = build_report(rows)
        assert report["n_events_total"] == 2
        assert report["n_events_included"] == 2
        assert set(report["by_horizon"].keys()) == {"1m", "3m", "6m", "12m"}
        h1 = report["by_horizon"]["1m"]
        assert set(h1.keys()) == {"overall", "by_action", "by_year", "by_holding_age", "by_concentration"}
        assert "BUY" in h1["by_action"] and "ADD" in h1["by_action"]
        assert "2020" in h1["by_year"] and "2021" in h1["by_year"]
        assert "<3M" in h1["by_holding_age"] and "12-24M" in h1["by_holding_age"]
        assert "<10%" in h1["by_concentration"] and "25-50%" in h1["by_concentration"]
        assert "drawdown_distribution" in report
        assert "data_quality_counts" in report
