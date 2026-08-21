from __future__ import annotations

"""Unit tests for the record-historical-financial-result CLI command via
Click's CliRunner. Mocks the service entry point and company/session
plumbing — same pattern as test_cli_phase5a.py/test_cli_phase5b.py."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from investing_agent.cli import cli
from investing_agent.services.ingestion.historical_financials import (
    HistoricalFinancialResult,
    HistoricalFinancialTranscriptionError,
)


class _FakeSessionCM:
    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc) -> bool:
        return False


def _fake_session_factory(session):
    return lambda: _FakeSessionCM(session)


def _fake_row(**overrides) -> MagicMock:
    defaults = dict(
        id=uuid.uuid4(), version=1, verification_status="verified",
        available_at=datetime(2022, 2, 10, 14, 25, 42, tzinfo=UTC),
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


class TestRecordHistoricalFinancialResultCommand:
    def test_bad_quarter_format_exits_before_touching_db(self) -> None:
        result = CliRunner().invoke(
            cli,
            [
                "record-historical-financial-result", "BEL",
                "--quarter", "bogus", "--source-document-id", str(uuid.uuid4()),
                "--revenue", "100",
            ],
        )
        assert result.exit_code == 1
        assert "Invalid --quarter format" in result.output

    def test_requires_at_least_one_figure(self) -> None:
        result = CliRunner().invoke(
            cli,
            [
                "record-historical-financial-result", "BEL",
                "--quarter", "Q3FY22", "--source-document-id", str(uuid.uuid4()),
            ],
        )
        assert result.exit_code == 1
        assert "provide at least" in result.output

    def test_successful_transcription_reports_created(self) -> None:
        session = AsyncMock()
        company = SimpleNamespace(id=uuid.uuid4(), symbol="BEL")
        doc_id = uuid.uuid4()
        row = _fake_row()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.ingestion.common.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.services.ingestion.historical_financials.record_historical_financial_result",
                AsyncMock(return_value=HistoricalFinancialResult(row=row, was_new_version=True)),
            ) as record_mock,
        ):
            result = CliRunner().invoke(
                cli,
                [
                    "record-historical-financial-result", "BEL",
                    "--quarter", "Q3FY22", "--scope", "STANDALONE",
                    "--source-document-id", str(doc_id), "--source-page", "12",
                    "--revenue", "435984.00", "--pbt", "117226.00", "--pat", "89330.00",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "Created" in result.output
        assert "verification_status=verified" in result.output
        record_mock.assert_awaited_once()
        _, kwargs = record_mock.call_args
        assert kwargs["fiscal_year"] == 2022
        assert kwargs["quarter"] == "Q3"
        assert kwargs["source_document_id"] == doc_id

    def test_transcription_error_rolls_back_and_exits(self) -> None:
        session = AsyncMock()
        company = SimpleNamespace(id=uuid.uuid4(), symbol="BEL")

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.ingestion.common.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.services.ingestion.historical_financials.record_historical_financial_result",
                AsyncMock(side_effect=HistoricalFinancialTranscriptionError("boom")),
            ),
        ):
            result = CliRunner().invoke(
                cli,
                [
                    "record-historical-financial-result", "BEL",
                    "--quarter", "Q3FY22", "--source-document-id", str(uuid.uuid4()),
                    "--revenue", "100",
                ],
            )

        assert result.exit_code == 1
        assert "boom" in result.output
        session.rollback.assert_awaited_once()
