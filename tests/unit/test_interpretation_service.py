from __future__ import annotations

"""Unit tests for services/interpretation/service.py::InterpretationService.

Repositories are mocked at the module-import-site (patch the class where
service.py imports it, same pattern as test_ingestion_services.py) so no
DB is touched; the LLM interpreter is a plain AsyncMock/MagicMock, never a
real ClaudeEventInterpreter.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from investing_agent.services.interpretation.service import InterpretationService

SINCE = datetime(2026, 8, 1, tzinfo=UTC)


def _event(headline: str, company_id: uuid.UUID | None) -> MagicMock:
    event = MagicMock()
    event.id = uuid.uuid4()
    event.representative_headline = headline
    event.event_type = "unclassified"
    event.primary_company_id = company_id
    return event


def _company(company_id: uuid.UUID) -> MagicMock:
    company = MagicMock()
    company.id = company_id
    company.name = "Bharat Electronics"
    company.symbol = "BEL"
    return company


class TestInterpretationServiceDeterministicPath:
    @pytest.mark.asyncio
    async def test_rule_match_used_and_llm_never_called(self) -> None:
        company_id = uuid.uuid4()
        event = _event("BEL wins order worth Rs 500 crore", company_id)

        fake_event_repo = MagicMock()
        fake_event_repo.list_by_company = AsyncMock(return_value=[event])
        fake_interp_repo = MagicMock()
        fake_interp_repo.list_by_event_and_company = AsyncMock(return_value=[])
        fake_interp_repo.create = AsyncMock()
        fake_company_repo = MagicMock()

        llm_interpreter = MagicMock()
        llm_interpreter.interpret = AsyncMock()

        with (
            patch(
                "investing_agent.services.interpretation.service.NewsEventRepository",
                return_value=fake_event_repo,
            ),
            patch(
                "investing_agent.services.interpretation.service.EventInterpretationRepository",
                return_value=fake_interp_repo,
            ),
            patch(
                "investing_agent.services.interpretation.service.CompanyRepository",
                return_value=fake_company_repo,
            ),
        ):
            service = InterpretationService(session=MagicMock(), llm_interpreter=llm_interpreter)
            result = await service.interpret_pending(company_id=company_id, since=SINCE)

        assert result.events_considered == 1
        assert result.interpreted_deterministic == 1
        assert result.interpreted_llm == 0
        llm_interpreter.interpret.assert_not_called()
        fake_interp_repo.create.assert_awaited_once()
        created_payload = fake_interp_repo.create.call_args.args[0]
        assert created_payload.news_event_id == event.id
        assert created_payload.company_id == company_id
        assert created_payload.extraction_method == "DETERMINISTIC"

    @pytest.mark.asyncio
    async def test_already_interpreted_event_is_skipped(self) -> None:
        company_id = uuid.uuid4()
        event = _event("BEL wins order worth Rs 500 crore", company_id)

        fake_event_repo = MagicMock()
        fake_event_repo.list_by_company = AsyncMock(return_value=[event])
        fake_interp_repo = MagicMock()
        fake_interp_repo.list_by_event_and_company = AsyncMock(return_value=[MagicMock()])
        fake_interp_repo.create = AsyncMock()
        fake_company_repo = MagicMock()

        with (
            patch(
                "investing_agent.services.interpretation.service.NewsEventRepository",
                return_value=fake_event_repo,
            ),
            patch(
                "investing_agent.services.interpretation.service.EventInterpretationRepository",
                return_value=fake_interp_repo,
            ),
            patch(
                "investing_agent.services.interpretation.service.CompanyRepository",
                return_value=fake_company_repo,
            ),
        ):
            service = InterpretationService(session=MagicMock(), llm_interpreter=None)
            result = await service.interpret_pending(company_id=company_id, since=SINCE)

        assert result.skipped_already_interpreted == 1
        fake_interp_repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_event_without_primary_company_is_skipped(self) -> None:
        event = _event("BEL wins order worth Rs 500 crore", company_id=None)

        fake_event_repo = MagicMock()
        fake_event_repo.list_since = AsyncMock(return_value=[event])
        fake_interp_repo = MagicMock()
        fake_interp_repo.create = AsyncMock()
        fake_company_repo = MagicMock()

        with (
            patch(
                "investing_agent.services.interpretation.service.NewsEventRepository",
                return_value=fake_event_repo,
            ),
            patch(
                "investing_agent.services.interpretation.service.EventInterpretationRepository",
                return_value=fake_interp_repo,
            ),
            patch(
                "investing_agent.services.interpretation.service.CompanyRepository",
                return_value=fake_company_repo,
            ),
        ):
            service = InterpretationService(session=MagicMock(), llm_interpreter=None)
            result = await service.interpret_pending(company_id=None, since=SINCE)

        assert result.events_considered == 1
        assert result.interpreted_deterministic == 0
        fake_interp_repo.create.assert_not_awaited()


class TestInterpretationServiceLLMFallback:
    @pytest.mark.asyncio
    async def test_llm_used_when_no_rule_matches(self) -> None:
        company_id = uuid.uuid4()
        event = _event("Company hosts annual sports day for employees", company_id)
        company = _company(company_id)

        fake_event_repo = MagicMock()
        fake_event_repo.list_by_company = AsyncMock(return_value=[event])
        fake_interp_repo = MagicMock()
        fake_interp_repo.list_by_event_and_company = AsyncMock(return_value=[])
        fake_interp_repo.create = AsyncMock()
        fake_company_repo = MagicMock()
        fake_company_repo.get = AsyncMock(return_value=company)

        llm_candidate = MagicMock()
        llm_candidate.impact_classification = {"revenue": {"direction": "neutral", "magnitude": "low"}}
        llm_candidate.rationale = "No material business impact."
        llm_candidate.candidate_catalyst = None
        llm_candidate.candidate_risk = None
        llm_candidate.candidate_thesis_change = None
        llm_candidate.extraction_method = "LLM_ASSISTED"
        llm_candidate.extractor_version = "llm-interpreter-v1:claude-sonnet-4-6"
        llm_candidate.confidence = "0.40"

        llm_interpreter = MagicMock()
        llm_interpreter.interpret = AsyncMock(return_value=llm_candidate)

        with (
            patch(
                "investing_agent.services.interpretation.service.NewsEventRepository",
                return_value=fake_event_repo,
            ),
            patch(
                "investing_agent.services.interpretation.service.EventInterpretationRepository",
                return_value=fake_interp_repo,
            ),
            patch(
                "investing_agent.services.interpretation.service.CompanyRepository",
                return_value=fake_company_repo,
            ),
        ):
            service = InterpretationService(session=MagicMock(), llm_interpreter=llm_interpreter)
            result = await service.interpret_pending(company_id=company_id, since=SINCE)

        assert result.interpreted_deterministic == 0
        assert result.interpreted_llm == 1
        llm_interpreter.interpret.assert_awaited_once_with(
            headline=event.representative_headline,
            company_name="Bharat Electronics",
            company_symbol="BEL",
            event_type="unclassified",
        )
        fake_interp_repo.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_llm_and_no_rule_match_is_skipped(self) -> None:
        company_id = uuid.uuid4()
        event = _event("Company hosts annual sports day for employees", company_id)

        fake_event_repo = MagicMock()
        fake_event_repo.list_by_company = AsyncMock(return_value=[event])
        fake_interp_repo = MagicMock()
        fake_interp_repo.list_by_event_and_company = AsyncMock(return_value=[])
        fake_interp_repo.create = AsyncMock()
        fake_company_repo = MagicMock()

        with (
            patch(
                "investing_agent.services.interpretation.service.NewsEventRepository",
                return_value=fake_event_repo,
            ),
            patch(
                "investing_agent.services.interpretation.service.EventInterpretationRepository",
                return_value=fake_interp_repo,
            ),
            patch(
                "investing_agent.services.interpretation.service.CompanyRepository",
                return_value=fake_company_repo,
            ),
        ):
            service = InterpretationService(session=MagicMock(), llm_interpreter=None)
            result = await service.interpret_pending(company_id=company_id, since=SINCE)

        assert result.skipped_no_interpretation == 1
        fake_interp_repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_llm_returns_none_is_skipped(self) -> None:
        company_id = uuid.uuid4()
        event = _event("Company hosts annual sports day for employees", company_id)
        company = _company(company_id)

        fake_event_repo = MagicMock()
        fake_event_repo.list_by_company = AsyncMock(return_value=[event])
        fake_interp_repo = MagicMock()
        fake_interp_repo.list_by_event_and_company = AsyncMock(return_value=[])
        fake_interp_repo.create = AsyncMock()
        fake_company_repo = MagicMock()
        fake_company_repo.get = AsyncMock(return_value=company)

        llm_interpreter = MagicMock()
        llm_interpreter.interpret = AsyncMock(return_value=None)

        with (
            patch(
                "investing_agent.services.interpretation.service.NewsEventRepository",
                return_value=fake_event_repo,
            ),
            patch(
                "investing_agent.services.interpretation.service.EventInterpretationRepository",
                return_value=fake_interp_repo,
            ),
            patch(
                "investing_agent.services.interpretation.service.CompanyRepository",
                return_value=fake_company_repo,
            ),
        ):
            service = InterpretationService(session=MagicMock(), llm_interpreter=llm_interpreter)
            result = await service.interpret_pending(company_id=company_id, since=SINCE)

        assert result.interpreted_llm == 0
        assert result.skipped_no_interpretation == 1
        fake_interp_repo.create.assert_not_awaited()


class TestInterpretationServiceCompanyScoping:
    @pytest.mark.asyncio
    async def test_no_company_filter_uses_list_since(self) -> None:
        fake_event_repo = MagicMock()
        fake_event_repo.list_since = AsyncMock(return_value=[])
        fake_event_repo.list_by_company = AsyncMock(return_value=[])
        fake_interp_repo = MagicMock()
        fake_company_repo = MagicMock()

        with (
            patch(
                "investing_agent.services.interpretation.service.NewsEventRepository",
                return_value=fake_event_repo,
            ),
            patch(
                "investing_agent.services.interpretation.service.EventInterpretationRepository",
                return_value=fake_interp_repo,
            ),
            patch(
                "investing_agent.services.interpretation.service.CompanyRepository",
                return_value=fake_company_repo,
            ),
        ):
            service = InterpretationService(session=MagicMock(), llm_interpreter=None)
            result = await service.interpret_pending(company_id=None, since=SINCE)

        fake_event_repo.list_since.assert_awaited_once_with(SINCE)
        fake_event_repo.list_by_company.assert_not_awaited()
        assert result.events_considered == 0
