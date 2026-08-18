from __future__ import annotations

"""Repositories for Phase 3B primary-source company intelligence tables.

Unlike FinancialResultRepository/CorporateActionRepository, these are NOT
versioned — each row is a distinct human-transcribed observation from a
primary document, not a correction of a prior row (see db.models module
docstring above the Phase 3B classes). create() is therefore a plain insert,
and list_by_company_as_of() is a simple available_at <= as_of filter with no
latest-per-group collapsing.
"""

import uuid
from datetime import datetime

from sqlalchemy import select

from investing_agent.db.models import (
    CapacityUpdate,
    ManagementCommentary,
    ManagementGuidance,
    OperationalMetric,
    OrderBookSnapshot,
    SegmentMetric,
)
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.company_research import (
    CapacityUpdateCreate,
    ManagementCommentaryCreate,
    ManagementGuidanceCreate,
    OperationalMetricCreate,
    OrderBookSnapshotCreate,
    SegmentMetricCreate,
)


class OrderBookSnapshotRepository(BaseRepository[OrderBookSnapshot]):
    model = OrderBookSnapshot

    async def create(self, data: OrderBookSnapshotCreate) -> OrderBookSnapshot:
        row = OrderBookSnapshot(**data.model_dump())
        return await self.add(row)

    async def list_by_company(self, company_id: uuid.UUID) -> list[OrderBookSnapshot]:
        result = await self.session.execute(
            select(OrderBookSnapshot)
            .where(OrderBookSnapshot.company_id == company_id)
            .order_by(OrderBookSnapshot.as_of_date.desc())
        )
        return list(result.scalars().all())

    async def list_by_company_as_of(
        self, company_id: uuid.UUID, as_of: datetime
    ) -> list[OrderBookSnapshot]:
        result = await self.session.execute(
            select(OrderBookSnapshot)
            .where(
                OrderBookSnapshot.company_id == company_id,
                OrderBookSnapshot.available_at <= as_of,
            )
            .order_by(OrderBookSnapshot.as_of_date.desc())
        )
        return list(result.scalars().all())


class ManagementGuidanceRepository(BaseRepository[ManagementGuidance]):
    model = ManagementGuidance

    async def create(self, data: ManagementGuidanceCreate) -> ManagementGuidance:
        row = ManagementGuidance(**data.model_dump())
        return await self.add(row)

    async def list_by_company(self, company_id: uuid.UUID) -> list[ManagementGuidance]:
        result = await self.session.execute(
            select(ManagementGuidance)
            .where(ManagementGuidance.company_id == company_id)
            .order_by(ManagementGuidance.fiscal_year.desc())
        )
        return list(result.scalars().all())

    async def list_by_company_as_of(
        self, company_id: uuid.UUID, as_of: datetime
    ) -> list[ManagementGuidance]:
        result = await self.session.execute(
            select(ManagementGuidance)
            .where(
                ManagementGuidance.company_id == company_id,
                ManagementGuidance.available_at <= as_of,
            )
            .order_by(ManagementGuidance.fiscal_year.desc())
        )
        return list(result.scalars().all())


class SegmentMetricRepository(BaseRepository[SegmentMetric]):
    model = SegmentMetric

    async def create(self, data: SegmentMetricCreate) -> SegmentMetric:
        row = SegmentMetric(**data.model_dump())
        return await self.add(row)

    async def list_by_company(self, company_id: uuid.UUID) -> list[SegmentMetric]:
        result = await self.session.execute(
            select(SegmentMetric).where(SegmentMetric.company_id == company_id)
        )
        return list(result.scalars().all())

    async def list_by_company_as_of(
        self, company_id: uuid.UUID, as_of: datetime
    ) -> list[SegmentMetric]:
        result = await self.session.execute(
            select(SegmentMetric).where(
                SegmentMetric.company_id == company_id,
                SegmentMetric.available_at <= as_of,
            )
        )
        return list(result.scalars().all())


class OperationalMetricRepository(BaseRepository[OperationalMetric]):
    model = OperationalMetric

    async def create(self, data: OperationalMetricCreate) -> OperationalMetric:
        row = OperationalMetric(**data.model_dump())
        return await self.add(row)

    async def list_by_company(self, company_id: uuid.UUID) -> list[OperationalMetric]:
        result = await self.session.execute(
            select(OperationalMetric).where(OperationalMetric.company_id == company_id)
        )
        return list(result.scalars().all())

    async def list_by_company_as_of(
        self, company_id: uuid.UUID, as_of: datetime
    ) -> list[OperationalMetric]:
        result = await self.session.execute(
            select(OperationalMetric).where(
                OperationalMetric.company_id == company_id,
                OperationalMetric.available_at <= as_of,
            )
        )
        return list(result.scalars().all())


class CapacityUpdateRepository(BaseRepository[CapacityUpdate]):
    model = CapacityUpdate

    async def create(self, data: CapacityUpdateCreate) -> CapacityUpdate:
        row = CapacityUpdate(**data.model_dump())
        return await self.add(row)

    async def list_by_company(self, company_id: uuid.UUID) -> list[CapacityUpdate]:
        result = await self.session.execute(
            select(CapacityUpdate).where(CapacityUpdate.company_id == company_id)
        )
        return list(result.scalars().all())

    async def list_by_company_as_of(
        self, company_id: uuid.UUID, as_of: datetime
    ) -> list[CapacityUpdate]:
        result = await self.session.execute(
            select(CapacityUpdate).where(
                CapacityUpdate.company_id == company_id,
                CapacityUpdate.available_at <= as_of,
            )
        )
        return list(result.scalars().all())


class ManagementCommentaryRepository(BaseRepository[ManagementCommentary]):
    model = ManagementCommentary

    async def create(self, data: ManagementCommentaryCreate) -> ManagementCommentary:
        row = ManagementCommentary(**data.model_dump())
        return await self.add(row)

    async def list_by_company(self, company_id: uuid.UUID) -> list[ManagementCommentary]:
        result = await self.session.execute(
            select(ManagementCommentary).where(ManagementCommentary.company_id == company_id)
        )
        return list(result.scalars().all())

    async def list_by_company_as_of(
        self, company_id: uuid.UUID, as_of: datetime
    ) -> list[ManagementCommentary]:
        result = await self.session.execute(
            select(ManagementCommentary).where(
                ManagementCommentary.company_id == company_id,
                ManagementCommentary.available_at <= as_of,
            )
        )
        return list(result.scalars().all())
