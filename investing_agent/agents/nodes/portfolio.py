from __future__ import annotations
"""Portfolio node: fetch holdings from broker, compute weights, update state."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from investing_agent.agents.state import InvestmentState
from investing_agent.config.logging import get_logger
from investing_agent.gateway.base import BrokerGateway
from investing_agent.schemas.portfolio import BrokerPortfolioResponse

log = get_logger(__name__)


def _compute_weights(holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add portfolio_weight_pct to each holding in place."""
    total = sum(
        float(h.get("current_value") or 0) for h in holdings
    )
    for h in holdings:
        val = float(h.get("current_value") or 0)
        h["portfolio_weight_pct"] = round(val / total * 100, 4) if total > 0 else 0.0
    return holdings


async def portfolio_node(
    state: InvestmentState,
    broker: BrokerGateway,
) -> dict[str, Any]:
    """Fetch live holdings and compute portfolio metrics."""
    try:
        response: BrokerPortfolioResponse = await broker.get_holdings()

        holdings_raw = []
        total_value = Decimal("0")
        total_invested = Decimal("0")

        for h in response.holdings:
            current_value = h.last_price * h.quantity
            invested = h.average_price * h.quantity
            total_value += current_value
            total_invested += invested

            holdings_raw.append({
                "symbol": h.tradingsymbol,
                "isin": h.isin,
                "exchange": h.exchange,
                "quantity": h.quantity,
                "t1_quantity": h.t1_quantity,
                "average_price": float(h.average_price),
                "last_price": float(h.last_price),
                "current_value": float(current_value),
                "pnl": float(h.pnl),
                "pnl_pct": round(float(h.pnl) / float(invested) * 100, 4) if invested else 0.0,
                "portfolio_weight_pct": 0.0,  # computed below
            })

        _compute_weights(holdings_raw)

        total_pnl = total_value - total_invested
        pnl_pct = float(total_pnl / total_invested * 100) if total_invested else 0.0

        portfolio_dict = {
            "holdings": holdings_raw,
            "total_value": float(total_value),
            "total_invested": float(total_invested),
            "total_pnl": float(total_pnl),
            "pnl_pct": round(pnl_pct, 4),
            "fetched_at": response.fetched_at.isoformat(),
            "source": response.source,
        }

        freshness = dict(state.get("data_freshness", {}))
        freshness["portfolio"] = datetime.now(timezone.utc).isoformat()

        evidence = list(state.get("evidence", []))
        evidence.append({
            "source": response.source,
            "published_at": response.fetched_at.isoformat(),
            "url": None,
            "tier": 1,
            "excerpt": f"Portfolio fetched: {len(holdings_raw)} holdings, "
                       f"total value ₹{float(total_value):,.2f}",
        })

        log.info(
            "portfolio_node.complete",
            holdings_count=len(holdings_raw),
            total_value=float(total_value),
        )

        return {
            "portfolio": portfolio_dict,
            "data_freshness": freshness,
            "evidence": evidence,
        }

    except Exception as exc:
        log.error("portfolio_node.error", error=str(exc))
        errors = list(state.get("errors", []))
        errors.append(f"Portfolio fetch failed: {exc}")
        return {"errors": errors}
