from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from investing_agent.services.governed_policy import candidate_specs, validate_expanding
from investing_agent.services.walkforward.audit import AuditRow


def _row(year: int, symbol: str = "BEL", excess: Decimal = Decimal("0.05")) -> AuditRow:
    horizons = ("1m", "3m", "6m", "12m")
    return AuditRow(
        symbol=symbol,
        decision_at=date(year, 6, 15),
        decision_id=uuid.uuid4(),
        hold_decision_id=uuid.uuid4(),
        action="ADD",
        data_quality_status="CLEAN",
        reconstruction_warnings=[],
        outcome_status="SCORED",
        quantity_held=Decimal("20"),
        invested_capital=Decimal("2000"),
        hold_quantity_held=Decimal("10"),
        holding_age_days=200,
        concentration_pct=Decimal("0.10"),
        calendar_year=year,
        entry_price=Decimal("100"),
        stock_return=dict.fromkeys(horizons, Decimal("0.10")),
        benchmark_return=dict.fromkeys(horizons, Decimal("0.05")),
        excess_return=dict.fromkeys(horizons, excess),
        hold_stock_return=dict.fromkeys(horizons, Decimal("0.10")),
        max_drawdown_pct=Decimal("-0.05"),
        data_quality_notes=[],
        included_in_aggregate=True,
        exclusion_reason=None,
        trade_evidence=[],
    )


def test_expanding_folds_never_include_validation_year_in_training() -> None:
    rows = [_row(year) for year in range(2020, 2026) for _ in range(5)]
    spec = next(spec for spec in candidate_specs() if spec.rule_id == "add_after_6_to_12m_review")
    evaluation = validate_expanding(rows, spec)
    assert evaluation.folds
    assert all(fold.train_end_year < fold.validation_year for fold in evaluation.folds)
    assert all(fold.validation.n == 5 for fold in evaluation.folds if fold.validation_year >= 2021)


def test_candidate_is_rejected_when_evidence_depends_on_one_symbol() -> None:
    rows = [_row(year) for year in range(2020, 2026) for _ in range(5)]
    spec = next(spec for spec in candidate_specs() if spec.rule_id == "add_after_6_to_12m_review")
    evaluation = validate_expanding(rows, spec)
    assert evaluation.status == "REJECTED"
    assert "evidence depends on one symbol" in evaluation.rejection_reasons


def test_candidate_is_rejected_when_excluding_2020_does_not_outperform() -> None:
    rows = [_row(2020, symbol="BEL", excess=Decimal("0.10")) for _ in range(5)]
    rows += [_row(year, symbol=f"S{year}", excess=Decimal("-0.02")) for year in range(2021, 2026) for _ in range(5)]
    spec = next(spec for spec in candidate_specs() if spec.rule_id == "add_after_6_to_12m_review")
    evaluation = validate_expanding(rows, spec)
    assert evaluation.status == "REJECTED"
    assert "no positive excluding-2020 median excess return" in evaluation.rejection_reasons
