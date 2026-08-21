from __future__ import annotations

"""Unit tests for the Phase 6A broker-history CLI commands
(record-broker-account / import-broker-statement / broker-import-report)
via Click's CliRunner. Mocks the repository/service entry points and
session plumbing — same pattern as test_cli_historical_financials.py."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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


class TestRecordBrokerAccountCommand:
    def test_creates_account_reports_strategy_profile(self) -> None:
        session = AsyncMock()
        account = SimpleNamespace(id=uuid.uuid4(), strategy_profile="LONG_TERM")

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.broker_history.BrokerAccountRepository"
            ) as repo_cls,
        ):
            repo_cls.return_value.get_or_create = AsyncMock(return_value=(account, True))
            result = CliRunner().invoke(
                cli,
                ["record-broker-account", "--broker", "ZERODHA", "--account-label", "primary"],
            )

        assert result.exit_code == 0, result.output
        assert "Created" in result.output
        assert "strategy_profile=LONG_TERM" in result.output

    def test_existing_account_reports_already_exists(self) -> None:
        session = AsyncMock()
        account = SimpleNamespace(id=uuid.uuid4(), strategy_profile="MEDIUM_TERM")

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.broker_history.BrokerAccountRepository"
            ) as repo_cls,
        ):
            repo_cls.return_value.get_or_create = AsyncMock(return_value=(account, False))
            result = CliRunner().invoke(
                cli,
                ["record-broker-account", "--broker", "INDMONEY", "--account-label", "primary"],
            )

        assert result.exit_code == 0, result.output
        assert "Already exists" in result.output


class TestImportBrokerStatementCommand:
    def test_unregistered_broker_exits(self, tmp_path) -> None:
        file_path = tmp_path / "statement.csv"
        file_path.write_text("data")

        with patch(
            "investing_agent.services.ingestion.broker_history.get_importer",
            side_effect=KeyError("No StatementImporter registered for broker='ZERODHA'"),
        ):
            result = CliRunner().invoke(
                cli,
                [
                    "import-broker-statement", "--broker", "ZERODHA",
                    "--account-label", "primary", str(file_path),
                ],
            )

        assert result.exit_code == 1
        assert "ERROR" in result.output

    def test_missing_broker_account_exits(self, tmp_path) -> None:
        file_path = tmp_path / "statement.csv"
        file_path.write_text("data")
        session = AsyncMock()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.services.ingestion.broker_history.get_importer") as get_importer,
            patch(
                "investing_agent.db.repositories.broker_history.BrokerAccountRepository"
            ) as repo_cls,
        ):
            get_importer.return_value = object()
            repo_cls.return_value.get_by_label = AsyncMock(return_value=None)
            result = CliRunner().invoke(
                cli,
                [
                    "import-broker-statement", "--broker", "ZERODHA",
                    "--account-label", "primary", str(file_path),
                ],
            )

        assert result.exit_code == 1
        assert "record-broker-account first" in result.output

    def test_successful_import_reports_counts(self, tmp_path) -> None:
        file_path = tmp_path / "statement.csv"
        file_path.write_text("data")
        session = AsyncMock()
        account = SimpleNamespace(id=uuid.uuid4())
        run = SimpleNamespace(
            id=uuid.uuid4(), status="SUCCESS", source_type="zerodha_tradebook_csv",
            trades_created=3, trades_skipped_duplicate=1,
            cash_flows_created=0, cash_flows_skipped_duplicate=0,
            dividends_created=0, dividends_skipped_duplicate=0,
            date_range_start=None, date_range_end=None,
        )

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.services.ingestion.broker_history.get_importer") as get_importer,
            patch(
                "investing_agent.db.repositories.broker_history.BrokerAccountRepository"
            ) as repo_cls,
            patch(
                "investing_agent.services.ingestion.broker_history.import_service.import_statement_file",
                AsyncMock(return_value=run),
            ) as import_mock,
        ):
            get_importer.return_value = object()
            repo_cls.return_value.get_by_label = AsyncMock(return_value=account)
            result = CliRunner().invoke(
                cli,
                [
                    "import-broker-statement", "--broker", "ZERODHA",
                    "--account-label", "primary", str(file_path),
                ],
            )

        assert result.exit_code == 0, result.output
        assert "SUCCESS" in result.output
        assert "+3 created, 1 already existed" in result.output
        import_mock.assert_awaited_once()
        session.commit.assert_awaited()

    def test_import_failure_still_commits_failed_run_and_exits(self, tmp_path) -> None:
        file_path = tmp_path / "statement.csv"
        file_path.write_text("data")
        session = AsyncMock()
        account = SimpleNamespace(id=uuid.uuid4())

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.services.ingestion.broker_history.get_importer") as get_importer,
            patch(
                "investing_agent.db.repositories.broker_history.BrokerAccountRepository"
            ) as repo_cls,
            patch(
                "investing_agent.services.ingestion.broker_history.import_service.import_statement_file",
                AsyncMock(side_effect=NotImplementedError("column layout not inspected yet")),
            ),
        ):
            get_importer.return_value = object()
            repo_cls.return_value.get_by_label = AsyncMock(return_value=account)
            result = CliRunner().invoke(
                cli,
                [
                    "import-broker-statement", "--broker", "INDMONEY",
                    "--account-label", "primary", str(file_path),
                ],
            )

        assert result.exit_code == 1
        assert "column layout not inspected yet" in result.output
        session.commit.assert_awaited()  # FAILED run row preserved, not rolled back


class TestBrokerImportReportCommand:
    def test_no_account_reports_none(self) -> None:
        session = AsyncMock()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.broker_history.BrokerAccountRepository"
            ) as repo_cls,
        ):
            repo_cls.return_value.get_by_label = AsyncMock(return_value=None)
            result = CliRunner().invoke(
                cli,
                ["broker-import-report", "--broker", "ZERODHA", "--account-label", "primary"],
            )

        assert result.exit_code == 0, result.output
        assert "No BrokerAccount" in result.output
