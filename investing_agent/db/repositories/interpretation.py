from __future__ import annotations

"""Repository for Phase 4B event_interpretations.

Deliberately does not create Catalyst/RiskObservation/ThesisChange rows
itself — mark_accepted only records the review decision and the id of a
row already created by the caller (services/interpretation or the
review-interpretation CLI command), keeping "decide to accept" and "create
the resulting fact" as two explicit, separately-testable steps.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from investing_agent.db.models import EventInterpretation
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.interpretation import EventInterpretationCreate


class EventInterpretationRepository(BaseRepository[EventInterpretation]):
    model = EventInterpretation

    async def create(self, data: EventInterpretationCreate) -> EventInterpretation:
        row = EventInterpretation(**data.model_dump())
        return await self.add(row)

    async def list_by_event_and_company(
        self, news_event_id: uuid.UUID, company_id: uuid.UUID
    ) -> list[EventInterpretation]:
        result = await self.session.execute(
            select(EventInterpretation).where(
                EventInterpretation.news_event_id == news_event_id,
                EventInterpretation.company_id == company_id,
            )
        )
        return list(result.scalars().all())

    async def list_pending(
        self, company_id: uuid.UUID | None = None
    ) -> list[EventInterpretation]:
        stmt = select(EventInterpretation).where(EventInterpretation.review_status == "pending")
        if company_id is not None:
            stmt = stmt.where(EventInterpretation.company_id == company_id)
        stmt = stmt.order_by(EventInterpretation.created_at.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_company(self, company_id: uuid.UUID) -> list[EventInterpretation]:
        result = await self.session.execute(
            select(EventInterpretation)
            .where(EventInterpretation.company_id == company_id)
            .order_by(EventInterpretation.created_at.desc())
        )
        return list(result.scalars().all())

    async def mark_accepted(
        self,
        interpretation: EventInterpretation,
        *,
        reviewed_by: str,
        resulting_catalyst_id: uuid.UUID | None = None,
        resulting_risk_observation_id: uuid.UUID | None = None,
        resulting_thesis_change_id: uuid.UUID | None = None,
    ) -> EventInterpretation:
        interpretation.review_status = "accepted"
        interpretation.reviewed_at = datetime.now(UTC)
        interpretation.reviewed_by = reviewed_by
        interpretation.resulting_catalyst_id = resulting_catalyst_id
        interpretation.resulting_risk_observation_id = resulting_risk_observation_id
        interpretation.resulting_thesis_change_id = resulting_thesis_change_id
        await self.session.flush()
        return interpretation

    async def mark_rejected(
        self, interpretation: EventInterpretation, *, reviewed_by: str
    ) -> EventInterpretation:
        interpretation.review_status = "rejected"
        interpretation.reviewed_at = datetime.now(UTC)
        interpretation.reviewed_by = reviewed_by
        await self.session.flush()
        return interpretation
