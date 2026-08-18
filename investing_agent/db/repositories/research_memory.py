from __future__ import annotations

"""Repositories for Phase 4A relational research memory: research_notes,
thesis_changes, catalysts, risk_observations, source_reliability.

These tables use plain TimestampMixin (not ProvenanceMixin), so point-in-time
queries filter on created_at <= as_of — "was this row visible in our system
as of this timestamp" — consistent with the rest of the codebase's
point-in-time convention, just anchored on system-visibility rather than
publisher-provenance timestamps.

SourceReliability rows are system-managed (no *Create schema): they are
initialized on first contact with a source and updated by the ingestion
pipeline after every fetch attempt, never submitted by a caller.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from investing_agent.db.models import (
    Catalyst,
    ResearchNote,
    RiskObservation,
    SourceReliability,
    ThesisChange,
)
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.research_memory import (
    CatalystCreate,
    ResearchNoteCreate,
    RiskObservationCreate,
    ThesisChangeCreate,
)


class ResearchNoteRepository(BaseRepository[ResearchNote]):
    model = ResearchNote

    async def create(self, data: ResearchNoteCreate) -> ResearchNote:
        row = ResearchNote(**data.model_dump())
        return await self.add(row)

    async def list_by_company(
        self, company_id: uuid.UUID, since: datetime | None = None
    ) -> list[ResearchNote]:
        stmt = select(ResearchNote).where(ResearchNote.company_id == company_id)
        if since is not None:
            stmt = stmt.where(ResearchNote.effective_at >= since)
        stmt = stmt.order_by(ResearchNote.effective_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_company_as_of(
        self, company_id: uuid.UUID, as_of: datetime
    ) -> list[ResearchNote]:
        result = await self.session.execute(
            select(ResearchNote)
            .where(ResearchNote.company_id == company_id, ResearchNote.created_at <= as_of)
            .order_by(ResearchNote.effective_at.desc())
        )
        return list(result.scalars().all())


class ThesisChangeRepository(BaseRepository[ThesisChange]):
    model = ThesisChange

    async def create(self, data: ThesisChangeCreate) -> ThesisChange:
        row = ThesisChange(**data.model_dump())
        return await self.add(row)

    async def list_by_company(
        self, company_id: uuid.UUID, since: datetime | None = None
    ) -> list[ThesisChange]:
        stmt = select(ThesisChange).where(ThesisChange.company_id == company_id)
        if since is not None:
            stmt = stmt.where(ThesisChange.effective_at >= since)
        stmt = stmt.order_by(ThesisChange.effective_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_company_as_of(
        self, company_id: uuid.UUID, as_of: datetime
    ) -> list[ThesisChange]:
        result = await self.session.execute(
            select(ThesisChange)
            .where(ThesisChange.company_id == company_id, ThesisChange.created_at <= as_of)
            .order_by(ThesisChange.effective_at.desc())
        )
        return list(result.scalars().all())


class CatalystRepository(BaseRepository[Catalyst]):
    model = Catalyst

    async def create(self, data: CatalystCreate) -> Catalyst:
        row = Catalyst(**data.model_dump())
        return await self.add(row)

    async def list_by_company(self, company_id: uuid.UUID) -> list[Catalyst]:
        result = await self.session.execute(
            select(Catalyst)
            .where(Catalyst.company_id == company_id)
            .order_by(Catalyst.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_active(self, company_id: uuid.UUID) -> list[Catalyst]:
        result = await self.session.execute(
            select(Catalyst).where(
                Catalyst.company_id == company_id, Catalyst.status == "active"
            )
        )
        return list(result.scalars().all())


class RiskObservationRepository(BaseRepository[RiskObservation]):
    model = RiskObservation

    async def create(self, data: RiskObservationCreate) -> RiskObservation:
        row = RiskObservation(**data.model_dump())
        return await self.add(row)

    async def list_by_company(self, company_id: uuid.UUID) -> list[RiskObservation]:
        result = await self.session.execute(
            select(RiskObservation)
            .where(RiskObservation.company_id == company_id)
            .order_by(RiskObservation.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_active(self, company_id: uuid.UUID) -> list[RiskObservation]:
        result = await self.session.execute(
            select(RiskObservation).where(
                RiskObservation.company_id == company_id, RiskObservation.status == "active"
            )
        )
        return list(result.scalars().all())


class SourceReliabilityRepository(BaseRepository[SourceReliability]):
    model = SourceReliability

    async def get_by_source_name(self, source_name: str) -> SourceReliability | None:
        result = await self.session.execute(
            select(SourceReliability).where(SourceReliability.source_name == source_name)
        )
        return result.scalar_one_or_none()

    async def get_or_create_for_source(
        self, source_name: str, *, enabled: bool = True, research_only: bool = False
    ) -> SourceReliability:
        existing = await self.get_by_source_name(source_name)
        if existing:
            return existing
        row = SourceReliability(
            source_name=source_name, enabled=enabled, research_only=research_only
        )
        return await self.add(row)

    async def record_success(self, source_name: str) -> SourceReliability:
        row = await self.get_or_create_for_source(source_name)
        row.successful_fetches += 1
        row.last_success_at = datetime.now(UTC)
        await self.session.flush()
        return row

    async def record_failure(self, source_name: str) -> SourceReliability:
        row = await self.get_or_create_for_source(source_name)
        row.failed_fetches += 1
        row.last_failure_at = datetime.now(UTC)
        await self.session.flush()
        return row

    async def record_stale(self, source_name: str) -> SourceReliability:
        row = await self.get_or_create_for_source(source_name)
        row.stale_feed_count += 1
        await self.session.flush()
        return row
