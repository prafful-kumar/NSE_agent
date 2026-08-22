from datetime import date
from decimal import Decimal

from investing_agent.services.recommendation_context import ResolvedHolding


def test_resolved_holding_carries_reconstructed_provenance() -> None:
    holding = ResolvedHolding("BEL", Decimal("5"), Decimal("100"), Decimal("120"), Decimal("600"), Decimal("10"), date(2026, 8, 19))
    assert holding.provenance == "HELD_RECONSTRUCTED"
    assert holding.current_value == Decimal("600")
