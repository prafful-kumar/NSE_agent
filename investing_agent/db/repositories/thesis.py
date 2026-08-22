from __future__ import annotations
from sqlalchemy import exists, select
from sqlalchemy.orm import aliased

from investing_agent.db.models import InvestmentThesis
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.thesis import InvestmentThesisCreate, InvestmentThesisUpdate


class ThesisRepository(BaseRepository[InvestmentThesis]):
    model = InvestmentThesis

    async def get_active(self, user_id: str, symbol: str) -> InvestmentThesis | None:
        successor = aliased(InvestmentThesis)
        result = await self.session.execute(
            select(InvestmentThesis).where(
                InvestmentThesis.user_id == user_id,
                InvestmentThesis.symbol == symbol.upper(),
                InvestmentThesis.status.in_(["active", "watching"]),
                ~exists(select(successor.id).where(successor.supersedes_thesis_id == InvestmentThesis.id)),
            ).order_by(InvestmentThesis.thesis_version.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_all_for_user(self, user_id: str) -> list[InvestmentThesis]:
        result = await self.session.execute(
            select(InvestmentThesis)
            .where(InvestmentThesis.user_id == user_id)
            .order_by(InvestmentThesis.updated_at.desc())
        )
        return list(result.scalars().all())

    async def create(self, user_id: str, data: InvestmentThesisCreate) -> InvestmentThesis:
        thesis = InvestmentThesis(
            user_id=user_id,
            symbol=data.symbol.upper(),
            status=data.status,
            thesis=data.thesis,
            buy_reasons=data.buy_reasons,
            risk_factors=data.risk_factors,
            catalysts=data.catalysts,
            invalidation_conditions=data.invalidation_conditions,
            target_price_low=data.target_price_low,
            target_price_base=data.target_price_base,
            target_price_high=data.target_price_high,
            horizon_months=data.horizon_months,
            entry_price=data.entry_price,
            company_id=data.company_id,
            as_of=data.as_of,
            thesis_model_version=data.thesis_model_version,
            evidence_refs=data.evidence_refs,
        )
        return await self.add(thesis)

    async def update(
        self, thesis: InvestmentThesis, data: InvestmentThesisUpdate
    ) -> InvestmentThesis:
        """Append a successor version; thesis rows are deliberately immutable."""
        changes = data.model_dump(exclude_none=True)
        values = {
            "user_id": thesis.user_id,
            "company_id": thesis.company_id,
            "symbol": thesis.symbol,
            "status": thesis.status,
            "thesis": thesis.thesis,
            "buy_reasons": thesis.buy_reasons,
            "risk_factors": thesis.risk_factors,
            "catalysts": thesis.catalysts,
            "invalidation_conditions": thesis.invalidation_conditions,
            "target_price_low": thesis.target_price_low,
            "target_price_base": thesis.target_price_base,
            "target_price_high": thesis.target_price_high,
            "horizon_months": thesis.horizon_months,
            "entry_price": thesis.entry_price,
            "exit_price": thesis.exit_price,
            "outcome_notes": thesis.outcome_notes,
            "thesis_version": thesis.thesis_version + 1,
            "thesis_model_version": thesis.thesis_model_version,
            "as_of": thesis.as_of,
            "evidence_refs": thesis.evidence_refs,
            "supersedes_thesis_id": thesis.id,
        }
        values.update(changes)
        successor = InvestmentThesis(**values)
        return await self.add(successor)
