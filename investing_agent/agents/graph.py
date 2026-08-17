from __future__ import annotations
"""LangGraph research graph.

Phase 1 graph: router -> [portfolio | memory | events] -> decision
Later phases add: facts, news, earnings_estimator, valuation, risk, approval_interrupt

The graph is compiled without a checkpointer in Phase 1.
Phase 2 will add PostgreSQL checkpointer for persistence across sessions.

Human-in-the-loop (approval interrupt) is wired but only active when
requires_approval=True and broker execution is enabled.
"""

from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph

from investing_agent.agents.nodes.decision import decision_node
from investing_agent.agents.nodes.memory import memory_node
from investing_agent.agents.nodes.portfolio import portfolio_node
from investing_agent.agents.nodes.router import route_after_router, router_node
from investing_agent.agents.state import InvestmentState
from investing_agent.config.logging import get_logger
from investing_agent.gateway.base import BrokerGateway
from investing_agent.gateway.mock import MockBrokerGateway

log = get_logger(__name__)


def _stub_events_node(state: InvestmentState) -> dict[str, Any]:
    """Phase 1 stub: corporate events node returns empty list.
    Phase 4 will fetch from exchange sources."""
    return {"corporate_events": []}


def _stub_facts_node(state: InvestmentState) -> dict[str, Any]:
    """Phase 1 stub: fundamentals/filings node.
    Phase 3 will fetch from NSE/BSE filings."""
    return {}


def _stub_response_node(state: InvestmentState) -> dict[str, Any]:
    """Format the final response (placeholder for Phase 1)."""
    return {}


def build_graph(
    broker: BrokerGateway | None = None,
    session_factory: Any = None,
) -> Any:
    """Build and compile the investment research graph.

    Args:
        broker: BrokerGateway instance. Defaults to MockBrokerGateway.
        session_factory: Async SQLAlchemy session factory.
            Required for memory_node in production.

    Returns:
        Compiled LangGraph runnable.
    """
    if broker is None:
        broker = MockBrokerGateway()

    workflow = StateGraph(InvestmentState)

    # ── Node registration ─────────────────────────────────────────────────────
    workflow.add_node("router", router_node)

    # Portfolio node needs the broker injected
    async def _portfolio(state: InvestmentState) -> dict[str, Any]:
        return await portfolio_node(state, broker=broker)

    workflow.add_node("portfolio", _portfolio)

    # Memory node needs a DB session
    async def _memory(state: InvestmentState) -> dict[str, Any]:
        if session_factory is None:
            log.warning("memory_node.no_session_factory", msg="Memory node returning empty profile")
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
