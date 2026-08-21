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
        """Most recently created snapshot for this (company, period, cutoff)
        triple, regardless of input_fingerprint. For display/lookup only —
        not for cache-reuse decisions (multiple snapshots with different
        fingerprints can share this triple; see get_matching)."""
        result = await self.session.execute(
            select(FeatureSnapshot)
            .where(
                FeatureSnapshot.company_id == company_id,
                FeatureSnapshot.financial_period_id == financial_period_id,
                FeatureSnapshot.cutoff_at == cutoff_at,
            )
            .order_by(FeatureSnapshot.created_at.desc())
        )
        return result.scalars().first()

    async def get_matching(
        self,
        company_id: uuid.UUID,
        financial_period_id: uuid.UUID,
        cutoff_at: datetime,
        input_fingerprint: str,
        feature_builder_version: str,
    ) -> FeatureSnapshot | None:
        """The snapshot to reuse: same key AND built from the exact same
        underlying qualifying records. A different input_fingerprint (e.g.
        newly-transcribed history became visible at this cutoff since the
        cached row was built) never matches here, so get_or_create below
        creates a fresh row instead of serving stale data."""
        result = await self.session.execute(
            select(FeatureSnapshot).where(
                FeatureSnapshot.company_id == company_id,
                FeatureSnapshot.financial_period_id == financial_period_id,
                FeatureSnapshot.cutoff_at == cutoff_at,
                FeatureSnapshot.input_fingerprint == input_fingerprint,
                FeatureSnapshot.feature_builder_version == feature_builder_version,
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, data: FeatureSnapshotCreate) -> tuple[FeatureSnapshot, bool]:
        """Returns (row, reused). reused=True means an existing snapshot
        built from the exact same input_fingerprint was found and returned
        as-is; reused=False means a new row was just inserted — the caller
        (build_feature_snapshot) uses this for cache-invalidation
        diagnostics."""
        existing = await self.get_matching(
            data.company_id, data.financial_period_id, data.cutoff_at,
            data.input_fingerprint, data.feature_builder_version,
        )
        if existing is not None:
            return existing, True
        row = FeatureSnapshot(**data.model_dump())
        return await self.add(row), False


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
