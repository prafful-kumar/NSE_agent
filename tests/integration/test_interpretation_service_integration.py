from __future__ import annotations

"""Integration test for InterpretationService.interpret_pending against a
real PostgreSQL database — proves the full NewsEvent -> EventInterpretation
persistence path for both the deterministic-rule and LLM-fallback branches.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d

The LLM branch here uses a FakeEventInterpreter (no network, no Anthropic
API key needed) — this proves persistence wiring, not the real Claude
integration, which is deliberately untestable without a live key (see
services/interpretation/llm_interpreter.py module docstring).
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.news import NewsEventCreate
from investing_agent.services.interpretation.llm_interpreter import EventInterpreter
from investing_agent.services.interpretation.rules import InterpretationCandidate
from investing_agent.services.interpretation.service import InterpretationService

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
async def db_session():
    import os

    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://investing:investing@localhost:5433/investing_agent_test"
    )
    from investing_agent.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def bel(db_session):
    from investing_agent.db.repositories.company import CompanyRepository

    repo = CompanyRepository(db_session)
    return await repo.upsert(
        CompanyCreate(
            symbol=f"BEL-{uuid.uuid4().hex[:8]}", name="Bharat Electronics", exchange="NSE"
        )
    )


class FakeEventInterpreter(EventInterpreter):
    """Always returns a fixed candidate — no network, no API key."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def interpret(
        self, *, headline: str, company_name: str, company_symbol: str, event_type: str
    ) -> InterpretationCandidate | None:
        self.calls.append(headline)
        return InterpretationCandidate(
            impact_classification={"revenue": {"direction": "neutral", "magnitude": "low"}},
            rationale=f"LLM assessed: {headline}",
            candidate_catalyst=None,
            candidate_risk=None,
            candidate_thesis_change=None,
            extraction_method="LLM_ASSISTED",
            extractor_version="llm-interpreter-v1:fake",
            confidence=Decimal("0.45"),
        )


async def _make_event(db_session, bel, headline: str):
    from investing_agent.db.repositories.news import NewsEventRepository

    repo = NewsEventRepository(db_session)
    return await repo.create(
        NewsEventCreate(
            primary_company_id=bel.id,
            first_seen_at=NOW,
            last_seen_at=NOW,
            representative_headline=headline,
        )
    )


class TestInterpretationServiceDeterministicPersistence:
    async def test_rule_match_persists_pending_interpretation(self, db_session, bel) -> None:
        from investing_agent.db.repositories.interpretation import EventInterpretationRepository

        event = await _make_event(db_session, bel, "BEL wins order worth Rs 500 crore")
        await db_session.flush()

        service = InterpretationService(db_session, llm_interpreter=None)
        result = await service.interpret_pending(
            company_id=bel.id, since=NOW - timedelta(days=1)
        )
        await db_session.flush()

        assert result.interpreted_deterministic == 1

        interp_repo = EventInterpretationRepository(db_session)
        rows = await interp_repo.list_by_event_and_company(event.id, bel.id)
        assert len(rows) == 1
        assert rows[0].review_status == "pending"
        assert rows[0].extraction_method == "DETERMINISTIC"
        assert rows[0].candidate_catalyst is not None

    async def test_rerun_is_idempotent(self, db_session, bel) -> None:
        await _make_event(db_session, bel, "BEL wins order worth Rs 500 crore")
        await db_session.flush()

        service = InterpretationService(db_session, llm_interpreter=None)
        first = await service.interpret_pending(company_id=bel.id, since=NOW - timedelta(days=1))
        await db_session.flush()
        second = await service.interpret_pending(company_id=bel.id, since=NOW - timedelta(days=1))
        await db_session.flush()

        assert first.interpreted_deterministic == 1
        assert second.interpreted_deterministic == 0
        assert second.skipped_already_interpreted == 1


class TestInterpretationServiceLLMFallbackPersistence:
    async def test_llm_fallback_persists_candidate(self, db_session, bel) -> None:
        from investing_agent.db.repositories.interpretation import EventInterpretationRepository

        event = await _make_event(
            db_session, bel, "Company hosts annual sports day for employees"
        )
        await db_session.flush()

        fake_llm = FakeEventInterpreter()
        service = InterpretationService(db_session, llm_interpreter=fake_llm)
        result = await service.interpret_pending(
            company_id=bel.id, since=NOW - timedelta(days=1)
        )
        await db_session.flush()

        assert result.interpreted_deterministic == 0
        assert result.interpreted_llm == 1
        assert fake_llm.calls == ["Company hosts annual sports day for employees"]

        interp_repo = EventInterpretationRepository(db_session)
        rows = await interp_repo.list_by_event_and_company(event.id, bel.id)
        assert len(rows) == 1
        assert rows[0].review_status == "pending"
        assert rows[0].extraction_method == "LLM_ASSISTED"
