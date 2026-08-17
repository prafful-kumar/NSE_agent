from __future__ import annotations
"""Memory node: retrieve user profile and active investment theses.

Phase 1: reads from PostgreSQL (relational retrieval).
Phase 3: will add pgvector semantic retrieval for similar past decisions.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.agents.state import InvestmentState
from investing_agent.config.logging import get_logger
from investing_agent.db.models import InvestmentThesis, UserPreference

log = get_logger(__name__)


async def memory_node(
    state: InvestmentState,
    session: AsyncSession,
) -> dict[str, Any]:
    """Retrieve user profile and theses for requested symbols."""
    user_id = state["user_id"]
    symbols = state.get("symbols", [])

    # ── User profile ──────────────────────────────────────────────────────────
    pref_result = await session.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )
    preference = pref_result.scalar_one_or_none()

    user_profile: dict[str, Any] = {}
    if preference:
        user_profile = {
            "preferred_sectors": preference.preferred_sectors,
            "avoided_sectors": preference.avoided_sectors,
            "avoided_stocks": preference.avoided_stocks,
            "preferred_holding_period_months": preference.preferred_holding_period_months,
            "risk_tolerance": preference.risk_tolerance,
            "max_stock_allocation_pct": (
                float(preference.max_stock_allocation_pct)
                if preference.max_stock_allocation_pct is not None
                else None
            ),
            "max_sector_allocation_pct": (
                float(preference.max_sector_allocation_pct)
                if preference.max_sector_allocation_pct is not None
                else None
            ),
            "valuation_preference": preference.valuation_preference,
        }

    # ── Investment theses for requested symbols ────────────────────────────────
    theses: dict[str, Any] = {}
    if symbols:
        thesis_result = await session.execute(
            select(InvestmentThesis).where(
                InvestmentThesis.user_id == user_id,
                InvestmentThesis.symbol.in_(symbols),
                InvestmentThesis.status.in_(["active", "watching"]),
            )
        )
        for thesis in thesis_result.scalars().all():
            theses[thesis.symbol] = {
                "status": thesis.status,
                "thesis": thesis.thesis,
                "buy_reasons": thesis.buy_reasons,
                "risk_factors": thesis.risk_factors,
                "catalysts": thesis.catalysts,
                "invalidation_conditions": thesis.invalidation_conditions,
                "target_price_base": (
                    float(thesis.target_price_base)
                    if thesis.target_price_base is not None
                    else None
                ),
                "horizon_months": thesis.horizon_months,
            }

    log.info(
        "memory_node.complete",
        user_id=user_id,
        symbols=symbols,
        has_profile=bool(user_profile),
        theses_found=list(theses.keys()),
    )

    company_facts = dict(state.get("company_facts", {}))
    for symbol in symbols:
        if symbol not in company_facts:
            company_facts[symbol] = {}
        company_facts[symbol]["thesis"] = theses.get(symbol)

    return {
        "user_profile": user_profile,
        "company_facts": company_facts,
    }
