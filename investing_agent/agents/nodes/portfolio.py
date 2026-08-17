from __future__ import annotations

"""Portfolio node: serve portfolio data from PostgreSQL.

Architecture (Phase 2):
    Zerodha MCP ──[sync-portfolio CLI / POST /portfolio/sync]──► PostgreSQL
                                                                      │
                                                               portfolio_node reads here
                                                                      │
                                                               LangGraph state["portfolio"]

The node NEVER hits Zerodha directly for every query.
It reads the latest persisted snapshot and reports data_freshness.
On-demand refresh is triggered explicitly by the user or CLI sync.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.agents.state import InvestmentState
from investing_agent.config.logging import get_logger
from investing_agent.config.settings import get_settings
from investing_agent.db.repositories.portfolio import PortfolioRepository

log = get_logger(__name__)
settings = get_settings()

_ZERO = Decimal("0")


def _is_stale(snapshot_time: datetime | None, max_age_seconds: int) -> bool:
    if snapshot_time is None:
        return True
    age = datetime.now(timezone.utc) - snapshot_time.replace(tzinfo=timezone.utc)
    return age.total_seconds() > max_age_seconds


async def portfolio_node(
    state: InvestmentState,
    session: AsyncSession,
) -> dict[str, Any]:
    """Load the latest portfolio snapshot from the database."""
    user_id = state["user_id"]
    repo = PortfolioRepository(session)
    snapshot = await repo.get_latest(user_id)

    errors = list(state.get("errors", []))
    evidence = list(state.get("evidence", []))

    if not snapshot:
        errors.append(
            "No portfolio snapshot found. "
            "Run: python -m investing_agent.cli sync-portfolio"
        )
        log.warning("portfolio_node.no_snapshot", user=user_id)
        return {"portfolio": None, "errors": errors}

    is_stale = _is_stale(snapshot.source_timestamp, settings.portfolio_staleness_seconds)
    if is_stale:
        errors.append(
            f"Portfolio data is stale (last synced: {snapshot.snapshot_date}). "
            "Run sync-portfolio for fresh data."
        )

    holdings_list = []
    for h in snapshot.holdings:
        holdings_list.append({
            "symbol": h.symbol,
            "isin": h.isin,
            "exchange": getattr(h, "exchange", "NSE"),
            "quantity": h.quantity,
            "t1_quantity": h.t1_quantity,
            "average_price": float(h.average_price),
            "last_price": float(h.last_price or _ZERO),
            "current_value": float(h.current_value or _ZERO),
            "invested_value": float((h.average_price or _ZERO) * h.quantity),
            "pnl": float(h.pnl or _ZERO),
            "pnl_pct": float(h.pnl_pct or _ZERO),
            "portfolio_weight_pct": float(h.portfolio_weight_pct or _ZERO),
        })

    portfolio_dict = {
        "holdings": sorted(holdings_list, key=lambda h: h["portfolio_weight_pct"], reverse=True),
        "total_value": float(snapshot.total_value or _ZERO),
        "total_invested": float(snapshot.total_invested or _ZERO),
        "total_pnl": float(snapshot.total_pnl or _ZERO),
        "pnl_pct": float(snapshot.pnl_pct or _ZERO),
        "snapshot_date": snapshot.snapshot_date.isoformat(),
        "source": snapshot.source,
        "fetched_at": (
            snapshot.source_timestamp.isoformat()
            if snapshot.source_timestamp
            else snapshot.created_at.isoformat()
        ),
        "is_stale": is_stale,
    }

    freshness = dict(state.get("data_freshness", {}))
    freshness["portfolio"] = portfolio_dict["fetched_at"]

    evidence.append({
        "source": snapshot.source,
        "published_at": portfolio_dict["fetched_at"],
        "url": None,
        "tier": 1,
        "category": "fact",
        "excerpt": (
            f"Portfolio snapshot: {len(holdings_list)} holdings, "
            f"total value ₹{float(snapshot.total_value or _ZERO):,.2f}"
            + (" [STALE]" if is_stale else "")
        ),
    })

    log.info(
        "portfolio_node.served_from_db",
        user=user_id,
        holdings=len(holdings_list),
        snapshot_date=snapshot.snapshot_date,
        is_stale=is_stale,
    )

    return {
        "portfolio": portfolio_dict,
        "data_freshness": freshness,
        "evidence": evidence,
        "errors": errors,
    }
