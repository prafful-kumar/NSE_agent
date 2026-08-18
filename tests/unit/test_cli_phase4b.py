from __future__ import annotations

"""Unit tests for the Phase 4B CLI commands (interpret-events,
list-interpretations, review-interpretation) via Click's CliRunner.

The DB/session layer is mocked throughout, same pattern as
tests/unit/test_cli_phase3b.py — these exercise CLI argument parsing and
the accept/reject review contract, not real persistence (covered by
tests/integration/test_interpretation_repository.py).
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from investing_agent.cli import cli


class _FakeSessionCM:
    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc) -> bool:
        return False


def _fake_session_factory(session):
    return lambda: _FakeSessionCM(session)


class TestInterpretEventsCommand:
    def test_no_llm_flag_skips_claude_construction(self) -> None:
        session = AsyncMock()
        run_result = SimpleNamespace(
            events_considered=2, interpreted_deterministic=1, interpreted_llm=0,
            skipped_already_interpreted=0, skipped_no_interpretation=1,
        )

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.interpretation.service.InterpretationService"
            ) as service_cls,
            patch(
                "investing_agent.services.interpretation.llm_interpreter.ClaudeEventInterpreter"
            ) as claude_cls,
        ):
            service_cls.return_value.interpret_pending = AsyncMock(return_value=run_result)
            result = CliRunner().invoke(cli, ["interpret-events", "--no-llm"])

        assert result.exit_code == 0, result.output
        claude_cls.assert_not_called()
        assert "Interpreted (deterministic): 1" in result.output

    def test_missing_api_key_prints_note_and_skips_llm(self) -> None:
        session = AsyncMock()
        run_result = SimpleNamespace(
            events_considered=0, interpreted_deterministic=0, interpreted_llm=0,
            skipped_already_interpreted=0, skipped_no_interpretation=0,
        )
        fake_settings = SimpleNamespace(
            anthropic_api_key=None, anthropic_model="claude-sonnet-4-6",
            log_level="INFO", is_development=True,
        )

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.cli.get_settings", return_value=fake_settings),
            patch(
                "investing_agent.services.interpretation.service.InterpretationService"
            ) as service_cls,
        ):
            service_cls.return_value.interpret_pending = AsyncMock(return_value=run_result)
            result = CliRunner().invoke(cli, ["interpret-events"])

        assert result.exit_code == 0, result.output
        assert "ANTHROPIC_API_KEY not set" in result.output


class TestListInterpretationsCommand:
    def test_requires_company_unless_status_pending(self) -> None:
        session = AsyncMock()
        with patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)):
            result = CliRunner().invoke(
                cli, ["list-interpretations", "--status", "accepted"]
            )
        assert result.exit_code == 1

    def test_lists_pending_interpretations(self) -> None:
        session = AsyncMock()
        row = MagicMock(
            id=uuid.uuid4(), news_event_id=uuid.uuid4(), extraction_method="DETERMINISTIC",
            confidence="0.60",
            impact_classification={"revenue": {"direction": "positive", "magnitude": "medium"}},
        )
        event = MagicMock(representative_headline="BEL wins order worth Rs 500 crore")

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.interpretation.EventInterpretationRepository"
            ) as interp_repo_cls,
            patch("investing_agent.db.repositories.news.NewsEventRepository") as event_repo_cls,
        ):
            interp_repo_cls.return_value.list_pending = AsyncMock(return_value=[row])
            event_repo_cls.return_value.get = AsyncMock(return_value=event)
            result = CliRunner().invoke(cli, ["list-interpretations"])

        assert result.exit_code == 0, result.output
        assert "BEL wins order worth Rs 500 crore" in result.output
        assert "revenue:positive/medium" in result.output


class TestReviewInterpretationCommand:
    def test_requires_exactly_one_of_accept_or_reject(self) -> None:
        result = CliRunner().invoke(
            cli, ["review-interpretation", str(uuid.uuid4()), "--reviewed-by", "analyst"]
        )
        assert result.exit_code == 1
        assert "exactly one" in result.output.lower()

    def test_reject_marks_rejected_without_creating_records(self) -> None:
        session = AsyncMock()
        interpretation_id = uuid.uuid4()
        interpretation = MagicMock(
            id=interpretation_id, review_status="pending",
            candidate_catalyst=None, candidate_risk=None, candidate_thesis_change=None,
        )

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.interpretation.EventInterpretationRepository"
            ) as interp_repo_cls,
        ):
            interp_repo_cls.return_value.get = AsyncMock(return_value=interpretation)
            interp_repo_cls.return_value.mark_rejected = AsyncMock(return_value=interpretation)
            result = CliRunner().invoke(
                cli,
                [
                    "review-interpretation", str(interpretation_id),
                    "--reject", "--reviewed-by", "analyst",
                ],
            )

        assert result.exit_code == 0, result.output
        interp_repo_cls.return_value.mark_rejected.assert_awaited_once()
        assert "Rejected interpretation" in result.output

    def test_accept_creates_catalyst_and_links_resulting_id(self) -> None:
        session = AsyncMock()
        interpretation_id = uuid.uuid4()
        company_id = uuid.uuid4()
        news_event_id = uuid.uuid4()
        interpretation = MagicMock(
            id=interpretation_id, review_status="pending", company_id=company_id,
            news_event_id=news_event_id,
            candidate_catalyst={
                "description": "Order win", "catalyst_type": "order_win", "status": "active",
            },
            candidate_risk=None, candidate_thesis_change=None,
        )
        created_catalyst = MagicMock(id=uuid.uuid4())

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.interpretation.EventInterpretationRepository"
            ) as interp_repo_cls,
            patch(
                "investing_agent.db.repositories.research_memory.CatalystRepository"
            ) as catalyst_repo_cls,
        ):
            interp_repo_cls.return_value.get = AsyncMock(return_value=interpretation)
            interp_repo_cls.return_value.mark_accepted = AsyncMock(return_value=interpretation)
            catalyst_repo_cls.return_value.create = AsyncMock(return_value=created_catalyst)

            result = CliRunner().invoke(
                cli,
                [
                    "review-interpretation", str(interpretation_id),
                    "--accept", "--reviewed-by", "analyst",
                ],
            )

        assert result.exit_code == 0, result.output
        catalyst_repo_cls.return_value.create.assert_awaited_once()
        create_call = catalyst_repo_cls.return_value.create.call_args
        created_data = create_call.args[0]
        assert created_data.company_id == company_id
        assert created_data.news_event_id == news_event_id
        assert created_data.catalyst_type == "order_win"

        mark_call = interp_repo_cls.return_value.mark_accepted.call_args
        assert mark_call.kwargs["resulting_catalyst_id"] == created_catalyst.id
        assert "catalyst created" in result.output

    def test_already_reviewed_interpretation_is_rejected_with_error(self) -> None:
        session = AsyncMock()
        interpretation_id = uuid.uuid4()
        interpretation = MagicMock(id=interpretation_id, review_status="accepted")

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.interpretation.EventInterpretationRepository"
            ) as interp_repo_cls,
        ):
            interp_repo_cls.return_value.get = AsyncMock(return_value=interpretation)
            result = CliRunner().invoke(
                cli,
                [
                    "review-interpretation", str(interpretation_id),
                    "--accept", "--reviewed-by", "analyst",
                ],
            )

        assert result.exit_code == 1

    def test_missing_interpretation_id_is_rejected_with_error(self) -> None:
        session = AsyncMock()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.interpretation.EventInterpretationRepository"
            ) as interp_repo_cls,
        ):
            interp_repo_cls.return_value.get = AsyncMock(return_value=None)
            result = CliRunner().invoke(
                cli,
                [
                    "review-interpretation", str(uuid.uuid4()),
                    "--accept", "--reviewed-by", "analyst",
                ],
            )

        assert result.exit_code == 1
