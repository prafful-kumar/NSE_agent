from __future__ import annotations

"""InterpretationService: NewsEvent -> EventInterpretation candidate.

Deterministic rules run first (services/interpretation/rules.py); only a
headline no rule matches falls back to the optional LLM interpreter. Both
paths always write review_status="pending" — see EventInterpretation's
docstring for why there is no bypass, not even for a confident rule match.

Idempotent per (news_event_id, company_id): an event that already has any
interpretation (pending, accepted, or rejected) is skipped, so re-running
interpret-events doesn't spam duplicate candidates for the same event. A
rejected interpretation is a human decision that stands; it is not
retried automatically.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.repositories.company import CompanyRepository
from investing_agent.db.repositories.interpretation import EventInterpretationRepository
from investing_agent.db.repositories.news import NewsEventRepository
from investing_agent.schemas.interpretation import EventInterpretationCreate
from investing_agent.services.interpretation.llm_interpreter import EventInterpreter
from investing_agent.services.interpretation.rules import interpret_deterministic

log = structlog.get_logger(__name__)


@dataclass
class InterpretationRunResult:
    events_considered: int = 0
    interpreted_deterministic: int = 0
    interpreted_llm: int = 0
    skipped_already_interpreted: int = 0
    skipped_no_interpretation: int = 0


class InterpretationService:
    def __init__(self, session: AsyncSession, llm_interpreter: EventInterpreter | None = None) -> None:
        self._session = session
        self._llm = llm_interpreter

    async def interpret_pending(
        self, *, company_id: uuid.UUID | None, since: datetime
    ) -> InterpretationRunResult:
        event_repo = NewsEventRepository(self._session)
        interp_repo = EventInterpretationRepository(self._session)
        company_repo = CompanyRepository(self._session)
        result = InterpretationRunResult()

        events = (
            await event_repo.list_by_company(company_id, since=since)
            if company_id is not None
            else await event_repo.list_since(since)
        )
        result.events_considered = len(events)

        for event in events:
            if event.primary_company_id is None:
                continue
            existing = await interp_repo.list_by_event_and_company(
                event.id, event.primary_company_id
            )
            if existing:
                result.skipped_already_interpreted += 1
                continue

            candidate = interpret_deterministic(event.representative_headline)
            if candidate is not None:
                result.interpreted_deterministic += 1
            elif self._llm is not None:
                company = await company_repo.get(event.primary_company_id)
                if company is None:
                    result.skipped_no_interpretation += 1
                    continue
                candidate = await self._llm.interpret(
                    headline=event.representative_headline,
                    company_name=company.name,
                    company_symbol=company.symbol,
                    event_type=event.event_type,
                )
                if candidate is not None:
                    result.interpreted_llm += 1

            if candidate is None:
                result.skipped_no_interpretation += 1
                log.info(
                    "interpretation.no_candidate",
                    news_event_id=str(event.id),
                    company_id=str(event.primary_company_id),
                )
                continue

            await interp_repo.create(
                EventInterpretationCreate(
                    news_event_id=event.id,
                    company_id=event.primary_company_id,
                    impact_classification=candidate.impact_classification,
                    rationale=candidate.rationale,
                    candidate_catalyst=candidate.candidate_catalyst,
                    candidate_risk=candidate.candidate_risk,
                    candidate_thesis_change=candidate.candidate_thesis_change,
                    extraction_method=candidate.extraction_method,
                    extractor_version=candidate.extractor_version,
                    confidence=candidate.confidence,
                )
            )

        return result
