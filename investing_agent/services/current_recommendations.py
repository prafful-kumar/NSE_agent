from __future__ import annotations
"""Phase 7A deterministic current recommendation engine (offline, no execution)."""
from datetime import UTC, datetime
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from investing_agent.db.models import (Company, EstimateRun, RecommendationDecision, RecommendationEvidenceLink, RecommendationRun, RiskObservation, ValuationSnapshot, WatchlistItem)
from investing_agent.db.repositories.portfolio import PortfolioRepository
from investing_agent.db.repositories.thesis import ThesisRepository

RULE_VERSION = "current-recommendation-v1"
STALE_VALUATION_DAYS = 90  # domain freshness guard, not outcome-tuned

async def generate(session: AsyncSession, *, user_id: str, as_of: datetime | None = None) -> RecommendationRun:
    as_of = as_of or datetime.now(UTC)
    snapshot = await PortfolioRepository(session).get_latest(user_id)
    held = {h.symbol.upper(): h for h in snapshot.holdings} if snapshot else {}
    watch = list((await session.execute(select(WatchlistItem).where(WatchlistItem.user_id == user_id, WatchlistItem.is_active.is_(True)))).scalars())
    symbols = sorted(set(held) | {w.symbol.upper() for w in watch})
    run = RecommendationRun(user_id=user_id, rule_version=RULE_VERSION, as_of=as_of, policy_layer_status="DISABLED")
    session.add(run); await session.flush()
    total = snapshot.total_value if snapshot and snapshot.total_value else Decimal("0")
    for symbol in symbols:
        company = (await session.execute(select(Company).where(Company.symbol == symbol))).scalar_one_or_none()
        holding = held.get(symbol)
        thesis = await ThesisRepository(session).get_active(user_id, symbol)
        gaps: list[str] = []
        risks = []
        valuation = None
        estimate = None
        if company:
            valuation = (await session.execute(select(ValuationSnapshot).where(ValuationSnapshot.company_id == company.id, ValuationSnapshot.as_of <= as_of).order_by(ValuationSnapshot.as_of.desc()).limit(1))).scalar_one_or_none()
            estimate = (await session.execute(select(EstimateRun).where(EstimateRun.company_id == company.id, EstimateRun.cutoff_at <= as_of).order_by(EstimateRun.cutoff_at.desc()).limit(1))).scalar_one_or_none()
            risks = list((await session.execute(select(RiskObservation).where(RiskObservation.company_id == company.id, RiskObservation.status == "active"))).scalars())
        if not company: gaps.append("company_not_resolved")
        if not thesis: gaps.append("active_thesis_missing")
        if not estimate: gaps.append("pit_estimate_missing")
        stale = valuation is None or (as_of - valuation.as_of).days > STALE_VALUATION_DAYS
        if stale: gaps.append("validated_valuation_missing_or_stale")
        hard_risk = any(r.severity.lower() in {"high", "critical"} for r in risks)
        action = "INSUFFICIENT_EVIDENCE"; reasons = []
        if hard_risk:
            action = "REDUCE" if holding else "AVOID"; reasons.append("active high/critical risk observation")
        elif thesis and thesis.status == "watching" and not holding:
            action = "WATCH"; reasons.append("watching thesis; no held position")
        elif holding:
            if not thesis: reasons.append("cannot assess held position without active thesis")
            elif stale: action = "HOLD"; reasons.append("thesis exists but valuation cannot support ADD distinction")
            else:
                weight = (holding.current_value / total) if total else Decimal("0")
                supportive = valuation.current_price is not None and valuation.fair_value_mid is not None and valuation.current_price < valuation.fair_value_mid
                if supportive and weight < Decimal("0.10") and estimate and estimate.confidence is not None:
                    action = "ADD"; reasons.append("active thesis, supportive valuation, estimate and acceptable concentration")
                else: action = "HOLD"; reasons.append("no validated incremental ADD trigger")
        elif thesis and not stale and estimate and valuation.current_price is not None and valuation.fair_value_mid is not None and valuation.current_price < valuation.fair_value_mid:
            action = "BUY"; reasons.append("active thesis, PIT estimate and supportive valuation")
        elif company:
            action = "WATCH" if not gaps else "INSUFFICIENT_EVIDENCE"; reasons.append("insufficient validated evidence for initiation")
        context = {"is_held": bool(holding), "concentration_pct": str((holding.current_value / total * 100) if holding and total else Decimal("0"))}
        decision = RecommendationDecision(run_id=run.id, company_id=company.id if company else None, symbol=symbol, base_action=action, final_action=action, confidence=(estimate.confidence if estimate else None), data_quality_status="GAPS" if gaps else "CLEAN", data_gaps=gaps, evidence_summary=reasons, valuation_inputs=({"id": str(valuation.id), "as_of": valuation.as_of.isoformat(), "method": valuation.valuation_method, "current_price": str(valuation.current_price), "fair_value_mid": str(valuation.fair_value_mid)} if valuation else None), risk_inputs={"active_count": len(risks), "hard_risk": hard_risk}, portfolio_context=context)
        session.add(decision); await session.flush()
        for typ, obj in (("thesis", thesis), ("estimate", estimate), ("valuation", valuation), *[("risk", r) for r in risks]):
            if obj: session.add(RecommendationEvidenceLink(recommendation_decision_id=decision.id, evidence_type=typ, evidence_id=obj.id, details=None))
    await session.flush(); return run
