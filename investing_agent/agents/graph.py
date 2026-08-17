from __future__ import annotations

"""LangGraph research graph — Phase 2.

Portfolio node now reads from PostgreSQL (not directly from broker).
Sync is triggered by CLI or API, not by agent queries.

Phase 1 nodes still present as stubs:
  facts, earnings_estimator, valuation, risk (→ Phase 3–6)
"""

from typing import Any

from langgraph.graph import END, StateGraph

from investing_agent.agents.nodes.decision import decision_node
from investing_agent.agents.nodes.memory import memory_node
from investing_agent.agents.nodes.portfolio import portfolio_node
from investing_agent.agents.nodes.router import route_after_router, router_node
from investing_agent.agents.state import InvestmentState
from investing_agent.config.logging import get_logger

log = get_logger(__name__)


def _stub_events_node(state: InvestmentState) -> dict[str, Any]:
    return {"corporate_events": []}


def _stub_facts_node(state: InvestmentState) -> dict[str, Any]:
    return {}


def build_graph(session_factory: Any = None) -> Any:
    """Build and compile the investment research graph.

    Args:
        session_factory: async SQLAlchemy session factory (AsyncSessionLocal).
            Required for portfolio and memory nodes.

    Returns:
        Compiled LangGraph runnable.
    """
    workflow = StateGraph(InvestmentState)

    # ── Node registration ─────────────────────────────────────────────────────
    workflow.add_node("router", router_node)

    # Portfolio node reads from DB
    async def _portfolio(state: InvestmentState) -> dict[str, Any]:
        if session_factory is None:
            log.warning("portfolio_node.no_session_factory")
            return {"portfolio": None, "errors": ["No database session available."]}
        async with session_factory() as session:
            return await portfolio_node(state, session=session)

    workflow.add_node("portfolio", _portfolio)

    # Memory node reads theses + user profile from DB
    async def _memory(state: InvestmentState) -> dict[str, Any]:
        if session_factory is None:
            log.warning("memory_node.no_session_factory")
            return {"user_profile": {}, "company_facts": {}}
        async with session_factory() as session:
            return await memory_node(state, session=session)

    workflow.add_node("memory", _memory)
    workflow.add_node("events", _stub_events_node)
    workflow.add_node("facts", _stub_facts_node)
    workflow.add_node("decision", decision_node)

    # ── Entry point ───────────────────────────────────────────────────────────
    workflow.set_entry_point("router")

    # ── Conditional routing after router ──────────────────────────────────────
    workflow.add_conditional_edges(
        "router",
        route_after_router,
        {
            "portfolio": "portfolio",
            "memory": "memory",
            "events": "events",
        },
    )

    # ── Standard flow ─────────────────────────────────────────────────────────
    workflow.add_edge("portfolio", "decision")
    workflow.add_edge("memory", "facts")
    workflow.add_edge("facts", "events")
    workflow.add_edge("events", "decision")
    workflow.add_edge("decision", END)

    compiled = workflow.compile()
    log.info("graph.compiled", nodes=list(workflow.nodes))
    return compiled
