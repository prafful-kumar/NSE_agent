from __future__ import annotations

"""Unit tests for services/interpretation/rules.py — the deterministic
(non-LLM) event interpretation layer. Pure functions, no DB, no LLM."""

from investing_agent.services.interpretation.rules import interpret_deterministic


class TestOrderWinRule:
    def test_matches_order_win_headline(self) -> None:
        candidate = interpret_deterministic("BEL wins order worth Rs 500 crore from Navy")
        assert candidate is not None
        assert candidate.extraction_method == "DETERMINISTIC"
        assert candidate.impact_classification["revenue"]["direction"] == "positive"
        assert candidate.impact_classification["order_book"]["direction"] == "positive"
        assert candidate.candidate_catalyst is not None
        assert candidate.candidate_catalyst["catalyst_type"] == "order_win"
        assert candidate.candidate_risk is None


class TestManagementResignationRule:
    def test_matches_resignation_headline(self) -> None:
        candidate = interpret_deterministic("CFO resigns from Bharat Electronics board")
        assert candidate is not None
        assert candidate.impact_classification["management"]["direction"] == "negative"
        assert candidate.candidate_risk is not None
        assert candidate.candidate_risk["risk_type"] == "management_change"
        assert candidate.candidate_catalyst is None


class TestDividendRule:
    def test_matches_dividend_headline(self) -> None:
        candidate = interpret_deterministic("BEL declares dividend of Rs 2 per share")
        assert candidate is not None
        assert candidate.impact_classification["capital_allocation"]["direction"] == "positive"
        assert candidate.candidate_thesis_change is not None
        assert candidate.candidate_catalyst is None
        assert candidate.candidate_risk is None


class TestBuybackRule:
    def test_matches_buyback_headline(self) -> None:
        candidate = interpret_deterministic("Board approves buyback of shares worth Rs 1000 crore")
        assert candidate is not None
        assert candidate.impact_classification["capital_allocation"]["direction"] == "positive"
        assert candidate.candidate_catalyst is not None
        assert candidate.candidate_catalyst["catalyst_type"] == "buyback"


class TestRegulatoryApprovalRule:
    def test_matches_approval_headline(self) -> None:
        candidate = interpret_deterministic("HAL receives approval for new aircraft variant")
        assert candidate is not None
        assert candidate.impact_classification["regulation"]["direction"] == "positive"
        assert candidate.candidate_catalyst is not None
        assert candidate.candidate_catalyst["catalyst_type"] == "regulatory_approval"


class TestPlantShutdownRule:
    def test_matches_shutdown_headline(self) -> None:
        candidate = interpret_deterministic("Company shuts plant temporarily after fire incident")
        assert candidate is not None
        assert candidate.impact_classification["revenue"]["direction"] == "negative"
        assert candidate.candidate_risk is not None
        assert candidate.candidate_risk["risk_type"] == "operational"


class TestRatingDowngradeRule:
    def test_matches_downgrade_headline(self) -> None:
        candidate = interpret_deterministic("Brokerage downgrades rating to Sell on valuation concerns")
        assert candidate is not None
        assert candidate.impact_classification["valuation"]["direction"] == "negative"
        assert candidate.candidate_risk is not None
        assert candidate.candidate_risk["risk_type"] == "rating_downgrade"


class TestCapexAnnouncementRule:
    def test_matches_capex_headline(self) -> None:
        candidate = interpret_deterministic("Company announces capex of Rs 300 crore for new plant")
        assert candidate is not None
        assert candidate.impact_classification["capital_allocation"]["direction"] == "neutral"
        assert candidate.candidate_catalyst is not None
        assert candidate.candidate_catalyst["catalyst_type"] == "capex"


class TestNoRuleMatch:
    def test_unrelated_headline_returns_none(self) -> None:
        candidate = interpret_deterministic("Company hosts annual sports day for employees")
        assert candidate is None

    def test_empty_headline_returns_none(self) -> None:
        assert interpret_deterministic("") is None


class TestRuleOrderPrecedence:
    def test_first_matching_rule_wins(self) -> None:
        # Deliberately crafted to hit both order-win and capex phrase lists;
        # order_win is listed first in _RULES, so it must win.
        headline = "Company wins order worth Rs 500 crore, to invest Rs 200 crore capex"
        candidate = interpret_deterministic(headline)
        assert candidate is not None
        assert candidate.candidate_catalyst["catalyst_type"] == "order_win"
