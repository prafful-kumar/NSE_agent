from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import InvestmentThesis
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.thesis import InvestmentThesisCreate, InvestmentThesisUpdate


class ThesisRepository(BaseRepository[InvestmentThesis]):
    model = InvestmentThesis

    async def get_active(self, user_id: str, symbol: str) -> InvestmentThesis | None:
        result = await self.session.execute(
            select(InvestmentThesis).where(
                InvestmentThesis.user_id == user_id,
                InvestmentThesis.symbol == symbol.upper(),
                InvestmentThesis.status.in_(["active", "watching"]),
            )
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
        )
        return await self.add(thesis)

    async def update(
        self, thesis: InvestmentThesis, data: InvestmentThesisUpdate
    ) -> InvestmentThesis:
        for field, value in data.model_dump(exclude_none=True).items():
            setattr(thesis, field, value)
        await self.session.flush()
        return thesis
