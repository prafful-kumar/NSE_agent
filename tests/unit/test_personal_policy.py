from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from investing_agent.services.personal_policy import build_patterns, ranked_impacts, sample_label
from investing_agent.services.walkforward.audit import AuditRow


def _row(
    *,
    action: str = "BUY",
    year: int = 2021,
    stock_return: Decimal = Decimal("0.10"),
    excess_return: Decimal = Decimal("0.02"),
    quantity: Decimal = Decimal("10"),
    hold_quantity: Decimal = Decimal("0"),
    symbol: str = "BEL",
) -> AuditRow:
    horizons = ("1m", "3m", "6m", "12m")
    return AuditRow(
        symbol=symbol,
        decision_at=date(year, 6, 15),
        decision_id=uuid.uuid4(),
        hold_decision_id=uuid.uuid4(),
        action=action,
        data_quality_status="CLEAN",
        reconstruction_warnings=[],
        outcome_status="SCORED",
        quantity_held=quantity,
        invested_capital=Decimal("1000"),
        hold_quantity_held=hold_quantity,
        holding_age_days=100,
        concentration_pct=Decimal("0.10"),
        calendar_year=year,
        entry_price=Decimal("100"),
        stock_return=dict.fromkeys(horizons, stock_return),
        benchmark_return=dict.fromkeys(horizons),
        excess_return=dict.fromkeys(horizons, excess_return),
        hold_stock_return=dict.fromkeys(horizons, stock_return),
        max_drawdown_pct=Decimal("-0.05"),
        data_quality_notes=[],
        included_in_aggregate=True,
        exclusion_reason=None,
        trade_evidence=[{"type": "historical_trade", "id": "trade-1"}],
    )


def test_sample_labels_follow_fixed_descriptive_boundaries() -> None:
    assert sample_label(4) == "INSUFFICIENT_EVIDENCE"
    assert sample_label(5) == "LOW_SAMPLE"
    assert sample_label(10) == "MODERATE_SAMPLE"
    assert sample_label(30) == "STRONGER_SAMPLE"


def test_patterns_include_full_history_and_exclude_2020() -> None:
    rows = [_row(year=2020), *[_row(year=2021) for _ in range(5)]]
    patterns = build_patterns(rows, sectors_by_symbol={}, regimes_by_decision_id={})
    full = next(
        pattern
        for pattern in patterns
        if pattern.population == "FULL_HISTORY"
        and pattern.dimension == "overall"
        and pattern.horizon == "12m"
    )
    excluding = next(
        pattern
        for pattern in patterns
        if pattern.population == "EXCLUDING_2020"
        and pattern.dimension == "overall"
        and pattern.horizon == "12m"
    )
    assert full.stats.n == 6
    assert excluding.stats.n == 5
    assert full.stats.sample_label == "LOW_SAMPLE"


def test_symbol_patterns_require_five_scored_observations() -> None:
    patterns = build_patterns(
        [_row(symbol="BEL") for _ in range(4)], sectors_by_symbol={}, regimes_by_decision_id={}
    )
    assert not [pattern for pattern in patterns if pattern.dimension == "symbol"]


def test_buy_and_exit_signs_represent_incremental_value_against_hold() -> None:
    buy = _row(quantity=Decimal("10"), hold_quantity=Decimal("0"), stock_return=Decimal("0.10"))
    # A sold position avoids the subsequent fall, yielding positive value
    # versus keeping those shares in the HOLD baseline.
    exit_on_fall = _row(
        action="EXIT",
        quantity=Decimal("0"),
        hold_quantity=Decimal("10"),
        stock_return=Decimal("-0.10"),
    )
    positive, negative = ranked_impacts([buy, exit_on_fall], "12m")
    assert len(positive) == 2
    assert not negative
    assert {event.decision_dollar_impact for event in positive} == {Decimal("100")}


def test_ranked_events_retain_phase_6d_audit_links() -> None:
    row = _row()
    positive, _negative = ranked_impacts([row], "1m")
    assert positive[0].decision_id == str(row.decision_id)
    assert positive[0].hold_decision_id == str(row.hold_decision_id)
    assert positive[0].trade_evidence == tuple(row.trade_evidence)
