from __future__ import annotations

"""Repositories for Phase 5A feature_snapshots and estimate_runs."""

import uuid
from datetime import datetime

from sqlalchemy import select

from investing_agent.db.models import EstimateRun, FeatureSnapshot
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.estimation import EstimateRunCreate, FeatureSnapshotCreate


class FeatureSnapshotRepository(BaseRepository[FeatureSnapshot]):
    model = FeatureSnapshot

    async def get_by_key(
        self, company_id: uuid.UUID, financial_period_id: uuid.UUID, cutoff_at: datetime
    ) -> FeatureSnapshot | None:
        result = await self.session.execute(
            select(FeatureSnapshot).where(
                FeatureSnapshot.company_id == company_id,
                FeatureSnapshot.financial_period_id == financial_period_id,
                FeatureSnapshot.cutoff_at == cutoff_at,
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, data: FeatureSnapshotCreate) -> FeatureSnapshot:
        existing = await self.get_by_key(
            data.company_id, data.financial_period_id, data.cutoff_at
        )
        if existing is not None:
            return existing
        row = FeatureSnapshot(**data.model_dump())
        return await self.add(row)


class EstimateRunRepository(BaseRepository[EstimateRun]):
    model = EstimateRun

    async def create(self, data: EstimateRunCreate) -> EstimateRun:
        row = EstimateRun(**data.model_dump())
        return await self.add(row)

    async def list_by_company(self, company_id: uuid.UUID) -> list[EstimateRun]:
        result = await self.session.execute(
            select(EstimateRun)
            .where(EstimateRun.company_id == company_id)
            .order_by(EstimateRun.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_by_period(self, financial_period_id: uuid.UUID) -> list[EstimateRun]:
        result = await self.session.execute(
            select(EstimateRun)
            .where(EstimateRun.financial_period_id == financial_period_id)
            .order_by(EstimateRun.created_at.desc())
        )
        return list(result.scalars().all())
