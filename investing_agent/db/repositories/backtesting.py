from __future__ import annotations

"""Repository for Phase 5B backtest_scores."""

import uuid

from sqlalchemy import select

from investing_agent.db.models import BacktestScore
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.backtesting import BacktestScoreCreate


class BacktestScoreRepository(BaseRepository[BacktestScore]):
    model = BacktestScore

    async def create(self, data: BacktestScoreCreate) -> BacktestScore:
        row = BacktestScore(**data.model_dump())
        return await self.add(row)

    async def list_by_company(self, company_id: uuid.UUID) -> list[BacktestScore]:
        result = await self.session.execute(
            select(BacktestScore)
            .where(BacktestScore.company_id == company_id)
            .order_by(BacktestScore.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_by_model_version(self, model_version: str) -> list[BacktestScore]:
        result = await self.session.execute(
            select(BacktestScore)
            .where(BacktestScore.model_version == model_version)
            .order_by(BacktestScore.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_all(self) -> list[BacktestScore]:
        result = await self.session.execute(
            select(BacktestScore).order_by(BacktestScore.created_at.desc())
        )
        return list(result.scalars().all())
