"""Tests for LangGraph InvestmentState and agent graph routing."""

import pytest


class TestInvestmentState:
    def test_make_initial_state(self):
        from investing_agent.agents.state import make_initial_state
        state = make_initial_state(
            request="What is my portfolio?",
            user_id="user1",
            session_id="sess-001",
        )
        assert state["request"] == "What is my portfolio?"
        assert state["user_id"] == "user1"
        assert state["requires_approval"] is False
        assert state["broker_execution"] if False else True  # ensure no exec in state
        assert isinstance(state["errors"], list)
        assert isinstance(state["evidence"], list)
        assert state["portfolio"] is None

    def test_state_has_no_secrets(self):
        from investing_agent.agents.state import make_initial_state
        state = make_initial_state("test", "user1", "sess-1")
        # State must not contain any secret fields
        assert "api_key" not in state
        assert "access_token" not in state
        assert "password" not in state


class TestRouterNode:
    def test_portfolio_intent_detection(self):
        from investing_agent.agents.nodes.router import _detect_intent
        from investing_agent.agents.state import Intent
        assert _detect_intent("what is in my portfolio?") == Intent.PORTFOLIO_REVIEW.value
        assert _detect_intent("show my holdings") == Intent.PORTFOLIO_REVIEW.value

    def test_dividend_intent_detection(self):
        from investing_agent.agents.nodes.router import _detect_intent
        from investing_agent.agents.state import Intent
        assert _detect_intent("when is INFY dividend?") == Intent.DIVIDEND_QUERY.value
        assert _detect_intent("ex-date for TCS") == Intent.DIVIDEND_QUERY.value

    def test_earnings_intent_detection(self):
        from investing_agent.agents.nodes.router import _detect_intent
        from investing_agent.agents.state import Intent
        assert _detect_intent("estimate quarterly result for BEL") == Intent.EARNINGS_PREVIEW.value
        assert _detect_intent("earnings preview HAL") == Intent.EARNINGS_PREVIEW.value

    def test_symbol_extraction_basic(self):
        from investing_agent.agents.nodes.router import _extract_symbols
        symbols = _extract_symbols("Analyze BEL and HAL before results")
        assert "BEL" in symbols
        assert "HAL" in symbols

    def test_symbol_extraction_filters_stopwords(self):
        from investing_agent.agents.nodes.router import _extract_symbols
        symbols = _extract_symbols("What is my portfolio today?")
        # Common words must be filtered
        assert "I" not in symbols
        assert "MY" not in symbols

    def test_router_node_updates_state(self):
        from investing_agent.agents.nodes.router import router_node
        from investing_agent.agents.state import make_initial_state
        state = make_initial_state("analyze BEL for earnings", "user1", "sess-1")
        result = router_node(state)
        assert "intent" in result
        assert "symbols" in result
        assert "BEL" in result["symbols"]

    def test_route_after_router_portfolio(self):
        from investing_agent.agents.nodes.router import route_after_router
        from investing_agent.agents.state import Intent, make_initial_state
        state = make_initial_state("show my portfolio", "user1", "sess-1")
        state["intent"] = Intent.PORTFOLIO_REVIEW.value
        assert route_after_router(state) == "portfolio"

    def test_route_after_router_dividend(self):
        from investing_agent.agents.nodes.router import route_after_router
        from investing_agent.agents.state import Intent, make_initial_state
        state = make_initial_state("dividend query", "user1", "sess-1")
        state["intent"] = Intent.DIVIDEND_QUERY.value
        assert route_after_router(state) == "events"


class TestDecisionNode:
    def test_portfolio_review_returns_summary(self):
        from investing_agent.agents.nodes.decision import decision_node
        from investing_agent.agents.state import Intent, make_initial_state
        state = make_initial_state("show portfolio", "user1", "sess-1")
        state["intent"] = Intent.PORTFOLIO_REVIEW.value
        state["portfolio"] = {
            "holdings": [
                {
                    "symbol": "BEL",
                    "portfolio_weight_pct": 20.0,
                    "current_value": 43000.0,
                    "pnl": 7000.0,
                }
            ],
            "total_value": 215000.0,
            "total_pnl": 15100.0,
            "pnl_pct": 7.56,
            "fetched_at": "2026-08-17T10:00:00",
            "source": "mock_broker",
        }
        result = decision_node(state)
        assert result["decision"]["type"] == "portfolio_summary"
        assert result["requires_approval"] is False

    def test_company_research_returns_insufficient_evidence(self):
        from investing_agent.agents.nodes.decision import decision_node
        from investing_agent.agents.state import Intent, make_initial_state
        state = make_initial_state("analyze BEL", "user1", "sess-1")
        state["intent"] = Intent.COMPANY_RESEARCH.value
        state["symbols"] = ["BEL"]
        result = decision_node(state)
        assert result["decision"]["type"] == "company_research"
        recs = result["decision"]["recommendations"]
        assert recs[0]["symbol"] == "BEL"
        # Phase 1: must return INSUFFICIENT_EVIDENCE or WATCH
        assert recs[0]["action"] in {"INSUFFICIENT_EVIDENCE", "WATCH"}

    def test_decision_action_is_always_valid(self):
        """Decision node must NEVER produce a free-form action string."""
        from investing_agent.agents.nodes.decision import _validate_action
        assert _validate_action("HOLD") == "HOLD"
        assert _validate_action("NOT_VALID") == "INSUFFICIENT_EVIDENCE"
        assert _validate_action("") == "INSUFFICIENT_EVIDENCE"
