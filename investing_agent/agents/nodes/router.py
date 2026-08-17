from __future__ import annotations
"""Router node: detect intent and extract symbols from the user request.

Uses simple heuristics in Phase 1.  Phase 4 will add LLM-based routing.
"""

import re
from typing import Any

from investing_agent.agents.state import Intent, InvestmentState
from investing_agent.config.logging import get_logger

log = get_logger(__name__)

# Known symbols we can extract by regex (uppercase 2-15 char words)
_SYMBOL_PATTERN = re.compile(r"\b([A-Z][A-Z0-9&-]{1,14})\b")

# Intent keyword mapping (order matters — more specific first)
_INTENT_KEYWORDS: list[tuple[list[str], Intent]] = [
    (["dividend", "ex-date", "ex date", "record date", "payout"], Intent.DIVIDEND_QUERY),
    (["calendar", "upcoming", "result date", "result schedule"], Intent.CALENDAR_QUERY),
    (["earnings", "quarterly result", "estimate", "q1", "q2", "q3", "q4", "result preview"], Intent.EARNINGS_PREVIEW),
    (["portfolio", "holdings", "my portfolio", "allocation", "what do i own"], Intent.PORTFOLIO_REVIEW),
    (["order", "buy", "sell", "place", "execute"], Intent.ORDER_REQUEST),
    (["analyze", "research", "thesis", "recommendation", "should i"], Intent.COMPANY_RESEARCH),
]


def _detect_intent(text: str) -> str:
    lower = text.lower()
    for keywords, intent in _INTENT_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return intent.value
    return Intent.GENERAL.value


def _extract_symbols(text: str) -> list[str]:
    """Extract likely stock symbols from free text.

    Conservative: returns only tokens that look like NSE symbols.
    Phase 4 will add LLM-backed entity resolution.
    """
    # Remove common English words that match the pattern
    _STOP = {
        "I", "MY", "ME", "THE", "AND", "OR", "FOR", "IN", "ON",
        "AT", "IS", "A", "AN", "TO", "BE", "BY", "OF", "DO",
        "ADD", "BUY", "ALL", "ANY", "GET", "CAN", "NEW", "TOP",
        "PAT", "EPS", "FCF", "ROE", "Q1", "Q2", "Q3", "Q4",
    }
    candidates = _SYMBOL_PATTERN.findall(text)
    return [c for c in candidates if c not in _STOP]


def router_node(state: InvestmentState) -> dict[str, Any]:
    """Detect intent and extract symbols; update state."""
    request = state["request"]
    intent = _detect_intent(request)
    symbols = _extract_symbols(request)

    log.info("router", intent=intent, symbols=symbols, request_preview=request[:80])

    return {
        "intent": intent,
        "symbols": symbols,
    }


def route_after_router(state: InvestmentState) -> str:
    """Conditional edge: route to the appropriate first node after router."""
    intent = state.get("intent", Intent.GENERAL.value)
    mapping = {
        Intent.PORTFOLIO_REVIEW.value: "portfolio",
        Intent.DIVIDEND_QUERY.value: "events",
        Intent.CALENDAR_QUERY.value: "events",
        # Everything else goes through memory -> facts
        Intent.COMPANY_RESEARCH.value: "memory",
        Intent.EARNINGS_PREVIEW.value: "memory",
        Intent.ORDER_REQUEST.value: "memory",
        Intent.GENERAL.value: "memory",
    }
    return mapping.get(intent, "memory")
