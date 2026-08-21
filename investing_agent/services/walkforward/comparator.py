from __future__ import annotations

"""Offline reporting for Phase 6H's deterministic baseline comparator."""

from collections import Counter
from decimal import Decimal

from investing_agent.services.walkforward.aggregation import _median

HORIZONS = ("1m", "3m", "6m", "12m")
HUNDRED = Decimal("100")


def build_comparator_report(entries) -> dict:
    by_key = {}
    for entry in entries:
        d = entry.decision
        by_key.setdefault((d.symbol, d.decision_at), {})[d.decision_source] = entry
    agent_rows = [v["AGENT"] for v in by_key.values() if "AGENT" in v]
    actions = Counter(row.decision.action for row in agent_rows)
    report = {
        "events": len(agent_rows),
        "action_distribution": dict(sorted(actions.items())),
        "policy_scoreable": sum(row.decision.action != "INSUFFICIENT_EVIDENCE" for row in agent_rows),
        "policy_unscoreable": sum(row.decision.action == "INSUFFICIENT_EVIDENCE" for row in agent_rows),
        "by_horizon": {},
    }
    for horizon in HORIZONS:
        scored = [r for r in agent_rows if getattr(r.outcome, f"stock_return_{horizon}") is not None]
        eligible = [r for r in scored if r.decision.action != "INSUFFICIENT_EVIDENCE"]
        stock = [getattr(r.outcome, f"stock_return_{horizon}") for r in eligible]
        excess = [getattr(r.outcome, f"excess_return_tri_{horizon}") for r in eligible]
        drawdowns = [r.outcome.max_drawdown_pct for r in eligible if r.outcome.max_drawdown_pct is not None]
        impacts = []
        actual_impacts = []
        for key, sources in by_key.items():
            agent, hold, actual = sources.get("AGENT"), sources.get("HOLD_BASELINE"), sources.get("ACTUAL")
            if not agent or not hold or not getattr(agent.outcome, f"stock_return_{horizon}"):
                continue
            move = agent.outcome.entry_price * getattr(agent.outcome, f"stock_return_{horizon}")
            impacts.append((agent.decision.quantity_held - hold.decision.quantity_held) * move)
            if actual and actual.outcome.entry_price and getattr(actual.outcome, f"stock_return_{horizon}") is not None:
                actual_impacts.append((actual.decision.quantity_held - hold.decision.quantity_held) * (actual.outcome.entry_price * getattr(actual.outcome, f"stock_return_{horizon}")))
        report["by_horizon"][horizon] = {
            "outcome_scoreable": len(scored),
            "outcome_unscoreable": len(agent_rows) - len(scored),
            "baseline_eligible": len(eligible),
            "median_stock_return_pct": (_median(stock) * HUNDRED) if stock else None,
            "median_excess_tri_pct": (_median([v for v in excess if v is not None]) * HUNDRED) if any(v is not None for v in excess) else None,
            "median_drawdown_pct": (_median(drawdowns) * HUNDRED) if drawdowns else None,
            "median_agent_vs_hold_impact_inr": _median(impacts),
            "median_actual_vs_hold_impact_inr": _median(actual_impacts),
        }
    return report
