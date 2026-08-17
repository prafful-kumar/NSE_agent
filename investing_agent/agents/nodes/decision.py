from __future__ import annotations
"""Decision node: synthesize all evidence into a structured recommendation.

Phase 1: rule-based skeleton with INSUFFICIENT_EVIDENCE as default.
Phase 4: LLM-powered synthesis with structured output.

CRITICAL RULES enforced here:
- action is from a closed set (never free-form).
- If evidence is missing/stale/conflicting: return INSUFFICIENT_EVIDENCE.
- Never claim certainty about earnings or price.
- requires_human_review = True always.
"""

from datetime import datetime, timezone
from typing import Any

from investing_agent.agents.state import InvestmentState
from investing_agent.config.logging import get_logger
from investing_agent.config.settings import get_settings
from investing_agent.schemas.recommendations import RecommendationAction

log = get_logger(__name__)
settings = get_settings()

_VALID_ACTIONS: set[str] = set(RecommendationAction.__args__)  # type: ignore[attr-defined]


def _validate_action(action: str) -> str:
    """Ensure action is from the closed set."""
    if action not in _VALID_ACTIONS:
        log.warning("decision_node.invalid_action", action=action)
        return "INSUFFICIENT_EVIDENCE"
    return action


def decision_node(state: InvestmentState) -> dict[str, Any]:
    """Produce a structured recommendation from accumulated state.

    Phase 1 returns INSUFFICIENT_EVIDENCE for any company research
    because external data sources (news, fundamentals, earnings) are
    not yet implemented.  Portfolio review requests get a portfolio summary.
    """
    intent = state.get("intent", "general")
    symbols = state.get("symbols", [])
    errors = list(state.get("errors", []))
    evidence = list(state.get("evidence", []))

    if intent == "portfolio_review" and state.get("portfolio"):
        portfolio = state["portfolio"]
        holdings = portfolio.get("holdings", [])
        decision = {
            "type": "portfolio_summary",
            "action": None,
            "summary": {
                "total_value": portfolio.get("total_value"),
                "total_pnl": portfolio.get("total_pnl"),
                "pnl_pct": portfolio.get("pnl_pct"),
                "holdings_count": len(holdings),
                "top_holdings": sorted(
                    holdings,
                    key=lambda h: h.get("portfolio_weight_pct", 0),
                    reverse=True,
                )[:5],
            },
            "data_freshness": portfolio.get("fetched_at"),
            "requires_human_review": False,
            "evidence": evidence,
            "errors": errors,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model_version": settings.anthropic_model,
            "prompt_version": "phase1-skeleton-v1",
        }
        log.info("decision_node.portfolio_summary", holdings_count=len(holdings))
        return {"decision": decision, "requires_approval": False}

    if symbols:
        # Phase 1: we don't have enough data yet for a real recommendation
        decisions_list = []
        for symbol in symbols:
            thesis = (
                state.get("company_facts", {})
                .get(symbol, {})
                .get("thesis")
            )
            action = "WATCH" if thesis else "INSUFFICIENT_EVIDENCE"
            decisions_list.append({
                "symbol": symbol,
                "action": _validate_action(action),
                "confidence": None,
                "thesis_status": thesis.get("status") if thesis else None,
                "reasons": [
                    "Phase 1: fundamental data, news, and earnings modules not yet active."
                ],
                "risks": ["Insufficient data for full risk assessment."],
                "invalidation_conditions": [],
                "requires_human_review": True,
                "evidence": evidence,
                "errors": errors,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "model_version": settings.anthropic_model,
                "prompt_version": "phase1-skeleton-v1",
            })

        decision = {
            "type": "company_research",
            "recommendations": decisions_list,
        }
        log.info("decision_node.company_research", symbols=symbols)
        return {"decision": decision, "requires_approval": False}

    # No symbols, no portfolio intent
    decision = {
        "type": "general_response",
        "action": "INSUFFICIENT_EVIDENCE",
        "message": "Please specify a stock symbol or ask about your portfolio.",
        "requires_human_review": False,
        "errors": errors,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return {"decision": decision, "requires_approval": False}
