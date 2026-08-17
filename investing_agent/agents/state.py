from __future__ import annotations
"""LangGraph state definition for the investment research agent.

InvestmentState is the single shared state object that flows through
every node in the graph.  Each node reads what it needs and writes
to its designated keys only.

Design rules:
- Never store secrets in state.
- Evidence list grows monotonically through the graph (append-only).
- errors list captures non-fatal errors (node continues); fatal errors raise.
- requires_approval blocks execution until human approves in the Approval node.
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, TypedDict

from langgraph.graph import add_messages


class Intent(str, Enum):
    PORTFOLIO_REVIEW = "portfolio_review"
    COMPANY_RESEARCH = "company_research"
    EARNINGS_PREVIEW = "earnings_preview"
    DIVIDEND_QUERY = "dividend_query"
    CALENDAR_QUERY = "calendar_query"
    ORDER_REQUEST = "order_request"
    GENERAL = "general"


class DataFreshness(TypedDict):
    """Per-source freshness tracking."""
    portfolio: str | None      # ISO datetime of last portfolio sync
    prices: str | None
    fundamentals: str | None
    news: str | None
    corporate_events: str | None
    external_research: str | None


class InvestmentState(TypedDict):
    # ── Request context ────────────────────────────────────────────────────────
    request: str                    # Raw user input
    session_id: str                 # Unique per conversation (for checkpointing)
    user_id: str                    # User identifier
    symbols: list[str]              # Extracted target symbols
    intent: str                     # Resolved Intent enum value

    # ── Portfolio data ─────────────────────────────────────────────────────────
    # None until portfolio node runs
    portfolio: dict[str, Any] | None

    # ── User context ───────────────────────────────────────────────────────────
    user_profile: dict[str, Any] | None

    # ── Company data ───────────────────────────────────────────────────────────
    company_facts: dict[str, Any]   # keyed by symbol

    # ── Events ────────────────────────────────────────────────────────────────
    corporate_events: list[dict[str, Any]]

    # ── Research ──────────────────────────────────────────────────────────────
    news: list[dict[str, Any]]
    external_research: list[dict[str, Any]]   # brokerage, YouTube

    # ── Analysis ──────────────────────────────────────────────────────────────
    earnings_estimate: dict[str, Any] | None
    valuation: dict[str, Any] | None
    risks: list[dict[str, Any]]

    # ── Decision ──────────────────────────────────────────────────────────────
    decision: dict[str, Any] | None
    requires_approval: bool         # True if a trade action is proposed

    # ── Audit trail ───────────────────────────────────────────────────────────
    # Evidence accumulates across nodes; never overwrite
    evidence: list[dict[str, Any]]
    errors: list[str]               # Non-fatal errors (shown in response)

    # ── Metadata ──────────────────────────────────────────────────────────────
    timestamp: str                  # ISO datetime when request was received
    data_freshness: DataFreshness


def make_initial_state(
    request: str,
    user_id: str,
    session_id: str,
    symbols: list[str] | None = None,
) -> InvestmentState:
    """Factory for a clean initial state."""
    return InvestmentState(
        request=request,
        session_id=session_id,
        user_id=user_id,
        symbols=symbols or [],
        intent=Intent.GENERAL.value,
        portfolio=None,
        user_profile=None,
        company_facts={},
        corporate_events=[],
        news=[],
        external_research=[],
        earnings_estimate=None,
        valuation=None,
        risks=[],
        decision=None,
        requires_approval=False,
        evidence=[],
        errors=[],
        timestamp=datetime.utcnow().isoformat(),
        data_freshness=DataFreshness(
            portfolio=None,
            prices=None,
            fundamentals=None,
            news=None,
            corporate_events=None,
            external_research=None,
        ),
    )
