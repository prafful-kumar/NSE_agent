from __future__ import annotations
"""Agent query API routes."""

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from investing_agent.agents.graph import build_graph
from investing_agent.agents.state import make_initial_state
from investing_agent.app.dependencies import BrokerDep, SettingsDep
from investing_agent.db.session import AsyncSessionLocal

router = APIRouter(prefix="/agent", tags=["agent"])


class QueryRequest(BaseModel):
    query: str
    symbols: list[str] | None = None


class QueryResponse(BaseModel):
    session_id: str
    intent: str
    decision: dict[str, Any] | None
    errors: list[str]
    evidence: list[dict[str, Any]]


@router.post("/query", response_model=QueryResponse)
async def agent_query(
    body: QueryRequest,
    settings: SettingsDep,
    broker: BrokerDep,
) -> QueryResponse:
    """Run the research graph for a free-form query."""
    session_id = str(uuid.uuid4())
    initial_state = make_initial_state(
        request=body.query,
        user_id=settings.default_user_id,
        session_id=session_id,
        symbols=body.symbols,
    )

    graph = build_graph(broker=broker, session_factory=AsyncSessionLocal)
    result = await graph.ainvoke(initial_state)

    return QueryResponse(
        session_id=session_id,
        intent=result.get("intent", "general"),
        decision=result.get("decision"),
        errors=result.get("errors", []),
        evidence=result.get("evidence", []),
    )
