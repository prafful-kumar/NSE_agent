from __future__ import annotations

"""EstimationService: orchestrates FeatureSnapshot -> DeterministicEstimate
-> optional LLM narrative -> persisted EstimateRun.

Python fixes every numeric field (via estimate_deterministic) before an
optional narrator is ever invoked. The narrator, when used, only ever
appends assumptions list entries tagged source="llm" — it has no way to
mutate a revenue/margin/pat/eps field, since NarrativeCandidate carries no
numeric field at all (see services/estimation/narrator.py).

Reused verbatim by Phase 5B's backtest runner with a historical cutoff_at —
that reuse is the whole point of separating snapshot/compute/persist into
this one orchestration entry point.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import EstimateRun
from investing_agent.db.repositories.estimation import EstimateRunRepository
from investing_agent.schemas.estimation import (
    AssumptionItem,
    EstimateRunCreate,
    FeatureSnapshotPayload,
)
from investing_agent.services.estimation.deterministic import estimate_deterministic
from investing_agent.services.estimation.features import build_feature_snapshot
from investing_agent.services.estimation.narrator import EstimateNarrator

_REVENUE_QUANT = Decimal("0.01")
_MARGIN_QUANT = Decimal("0.0001")
_EPS_QUANT = Decimal("0.0001")


def _q(value: Decimal | None, quant: Decimal) -> Decimal | None:
    return value.quantize(quant) if value is not None else None


class EstimationService:
    def __init__(self, session: AsyncSession, narrator: EstimateNarrator | None = None) -> None:
        self._session = session
        self._narrator = narrator

    async def generate_estimate(
        self,
        *,
        company_id: uuid.UUID,
        financial_period_id: uuid.UUID,
        cutoff_at: datetime,
        model_version: str = "deterministic-v1",
    ) -> EstimateRun:
        """Estimate the supplied target FinancialPeriod.

        The deterministic feature builder and estimator project exactly that
        one quarterly period, so eps_low/base/high have the explicit
        ``NEXT_QUARTER`` horizon. They must not be used as annual EPS.
        """
        snapshot_row = await build_feature_snapshot(
            self._session, company_id, financial_period_id, cutoff_at
        )
        snapshot = FeatureSnapshotPayload(**snapshot_row.payload)

        computed = estimate_deterministic(snapshot)
        assumptions = list(computed.assumptions)
        effective_model_version = model_version

        if self._narrator is not None:
            narrative = await self._narrator.narrate(computed=computed, snapshot=snapshot)
            if narrative is not None:
                effective_model_version = f"{model_version}+narrator-v1"
                assumptions += [
                    AssumptionItem(text=t, source="llm") for t in narrative.assumptions_llm
                ]
                assumptions += [
                    AssumptionItem(text=f"Risk flagged: {t}", source="llm")
                    for t in narrative.risks_flagged
                ]

        data = EstimateRunCreate(
            company_id=company_id,
            financial_period_id=financial_period_id,
            cutoff_at=cutoff_at,
            model_version=effective_model_version,
            earnings_horizon="NEXT_QUARTER",
            feature_snapshot_id=snapshot_row.id,
            revenue_low=_q(computed.revenue.low, _REVENUE_QUANT),
            revenue_base=_q(computed.revenue.base, _REVENUE_QUANT),
            revenue_high=_q(computed.revenue.high, _REVENUE_QUANT),
            ebitda_margin_low=_q(computed.ebitda_margin.low, _MARGIN_QUANT),
            ebitda_margin_base=_q(computed.ebitda_margin.base, _MARGIN_QUANT),
            ebitda_margin_high=_q(computed.ebitda_margin.high, _MARGIN_QUANT),
            pat_low=_q(computed.pat.low, _REVENUE_QUANT),
            pat_base=_q(computed.pat.base, _REVENUE_QUANT),
            pat_high=_q(computed.pat.high, _REVENUE_QUANT),
            eps_low=_q(computed.eps.low, _EPS_QUANT),
            eps_base=_q(computed.eps.base, _EPS_QUANT),
            eps_high=_q(computed.eps.high, _EPS_QUANT),
            confidence=computed.confidence,
            assumptions=assumptions,
        )
        return await EstimateRunRepository(self._session).create(data)
