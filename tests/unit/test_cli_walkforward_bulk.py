from __future__ import annotations

"""Unit tests for the Phase 6D CLI commands (walk-forward-bulk-run,
walk-forward-report) via Click's CliRunner. Mirrors test_cli_walkforward.py's
pattern: mock the service/repository entry points, let the CLI's own
parsing/formatting run for real."""

import csv
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from investing_agent.cli import cli
from investing_agent.services.walkforward.audit import AuditRow
from investing_agent.services.walkforward.runner import WalkForwardEntry, WalkForwardRunResult


class _FakeSessionCM:
    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc) -> bool:
        return False


def _fake_session_factory(session):
    return lambda: _FakeSessionCM(session)


def _fake_decision(**overrides) -> SimpleNamespace:
    defaults = dict(
        id=uuid.uuid4(), symbol="BEL", decision_at=date(2021, 6, 15),
        decision_source="ACTUAL", action="BUY", quantity_held=Decimal("100"),
        average_cost=Decimal("150"), invested_capital=Decimal("15000"),
        data_quality_status="CLEAN",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_outcome(**overrides) -> SimpleNamespace:
    defaults = dict(
        id=uuid.uuid4(), outcome_status="SCORED",
        entry_price=Decimal("150"), entry_price_date=date(2021, 6, 15),
        stock_return_1m=Decimal("0.05"), benchmark_return_1m=Decimal("0.02"), excess_return_1m=Decimal("0.03"),
        stock_return_3m=None, benchmark_return_3m=None, excess_return_3m=None,
        stock_return_6m=None, benchmark_return_6m=None, excess_return_6m=None,
        stock_return_12m=None, benchmark_return_12m=None, excess_return_12m=None,
        data_quality_notes=[],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_audit_row(**overrides) -> AuditRow:
    horizons = ("1m", "3m", "6m", "12m")
    defaults = dict(
        symbol="BEL", decision_at=date(2021, 6, 15), decision_id=uuid.uuid4(),
        hold_decision_id=uuid.uuid4(), action="BUY", data_quality_status="CLEAN",
        reconstruction_warnings=[], outcome_status="SCORED",
        quantity_held=Decimal("100"), invested_capital=Decimal("15000"),
        hold_quantity_held=Decimal("0"),
        holding_age_days=0, concentration_pct=Decimal("0.20"), calendar_year=2021,
        entry_price=Decimal("150"),
        stock_return={**dict.fromkeys(horizons), "1m": Decimal("0.05")},
        benchmark_return={**dict.fromkeys(horizons), "1m": Decimal("0.02")},
        excess_return={**dict.fromkeys(horizons), "1m": Decimal("0.03")},
        hold_stock_return=dict.fromkeys(horizons),
        max_drawdown_pct=Decimal("-0.10"), data_quality_notes=[],
        included_in_aggregate=True, exclusion_reason=None, trade_evidence=[],
    )
    defaults.update(overrides)
    return AuditRow(**defaults)


class TestWalkForwardBulkRunCommand:
    def test_no_account_found_exits_nonzero(self) -> None:
        session = AsyncMock()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.db.repositories.broker_history.BrokerAccountRepository") as repo_cls,
        ):
            repo_cls.return_value.get_by_label = AsyncMock(return_value=None)
            result = CliRunner().invoke(
                cli, ["walk-forward-bulk-run", "--broker", "ZERODHA", "--account-label", "primary"]
            )

        assert result.exit_code == 1
        assert "ERROR: no BrokerAccount" in result.output

    def test_valid_run_reports_decision_point_count_and_action_breakdown(self) -> None:
        session = AsyncMock()
        account = SimpleNamespace(id=uuid.uuid4(), strategy_profile="LONG_TERM")
        run = SimpleNamespace(id=uuid.uuid4())
        entries = [
            WalkForwardEntry(decision=_fake_decision(decision_source="ACTUAL", action="BUY"), outcome=_fake_outcome()),
            WalkForwardEntry(decision=_fake_decision(decision_source="HOLD_BASELINE", action="HOLD"), outcome=_fake_outcome()),
            WalkForwardEntry(
                decision=_fake_decision(symbol="TCS", decision_source="ACTUAL", action="ADD"), outcome=_fake_outcome()
            ),
            WalkForwardEntry(
                decision=_fake_decision(symbol="TCS", decision_source="HOLD_BASELINE", action="HOLD"), outcome=_fake_outcome()
            ),
        ]
        run_result = WalkForwardRunResult(run=run, entries=entries)

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.db.repositories.broker_history.BrokerAccountRepository") as repo_cls,
            patch(
                "investing_agent.services.walkforward.runner.run_bulk_walk_forward",
                AsyncMock(return_value=run_result),
            ) as run_fn,
        ):
            repo_cls.return_value.get_by_label = AsyncMock(return_value=account)
            result = CliRunner().invoke(
                cli, ["walk-forward-bulk-run", "--broker", "ZERODHA", "--account-label", "primary"]
            )

        assert result.exit_code == 0, result.output
        run_fn.assert_awaited_once()
        assert run_fn.await_args.kwargs["broker_account_id"] == account.id
        assert "2 decision points" in result.output
        assert "BUY=1" in result.output
        assert "ADD=1" in result.output

    def test_runner_exception_rolls_back_and_exits_nonzero(self) -> None:
        session = AsyncMock()
        account = SimpleNamespace(id=uuid.uuid4(), strategy_profile="LONG_TERM")

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.db.repositories.broker_history.BrokerAccountRepository") as repo_cls,
            patch(
                "investing_agent.services.walkforward.runner.run_bulk_walk_forward",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            repo_cls.return_value.get_by_label = AsyncMock(return_value=account)
            result = CliRunner().invoke(
                cli, ["walk-forward-bulk-run", "--broker", "ZERODHA", "--account-label", "primary"]
            )

        assert result.exit_code == 1
        assert "ERROR: boom" in result.output
        session.rollback.assert_awaited()


class TestWalkForwardReportCommand:
    def test_no_run_found_exits_nonzero(self) -> None:
        session = AsyncMock()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.db.repositories.walkforward.WalkForwardRunRepository") as run_repo_cls,
        ):
            run_repo_cls.return_value.get = AsyncMock(return_value=None)
            result = CliRunner().invoke(cli, ["walk-forward-report", "--run-id", str(uuid.uuid4())])

        assert result.exit_code == 1
        assert "ERROR: no WalkForwardRun" in result.output

    def test_prints_aggregate_report_and_writes_csv(self, tmp_path) -> None:
        session = AsyncMock()
        run = SimpleNamespace(
            id=uuid.uuid4(), broker_account_id=uuid.uuid4(), strategy_profile="LONG_TERM",
            horizons_months=[1, 3, 6, 12], model_version="walkforward-v1",
        )
        decision = _fake_decision()
        outcome = _fake_outcome()
        audit_rows = [_fake_audit_row()]
        csv_path = tmp_path / "audit.csv"

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.db.repositories.walkforward.WalkForwardRunRepository") as run_repo_cls,
            patch(
                "investing_agent.db.repositories.walkforward.WalkForwardDecisionRepository"
            ) as decision_repo_cls,
            patch(
                "investing_agent.db.repositories.walkforward.WalkForwardOutcomeRepository"
            ) as outcome_repo_cls,
            patch(
                "investing_agent.services.walkforward.audit.build_audit_rows",
                AsyncMock(return_value=audit_rows),
            ),
        ):
            run_repo_cls.return_value.get = AsyncMock(return_value=run)
            decision_repo_cls.return_value.list_for_run = AsyncMock(return_value=[decision])
            outcome_repo_cls.return_value.get_by_decision_id = AsyncMock(return_value=outcome)

            result = CliRunner().invoke(
                cli, ["walk-forward-report", "--run-id", str(run.id), "--csv-out", str(csv_path)]
            )

        assert result.exit_code == 0, result.output
        assert "events=1" in result.output
        assert "included=1" in result.output
        assert "== 1M ==" in result.output
        assert "by action" in result.output
        assert "by holding age" in result.output
        assert "by concentration" in result.output

        assert csv_path.exists()
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["symbol"] == "BEL"
        assert rows[0]["included_in_aggregate"] == "True"

    def test_empty_rows_prints_no_decisions_message(self) -> None:
        session = AsyncMock()
        run = SimpleNamespace(
            id=uuid.uuid4(), broker_account_id=uuid.uuid4(), strategy_profile="LONG_TERM",
            horizons_months=[1, 3, 6, 12], model_version="walkforward-v1",
        )

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.db.repositories.walkforward.WalkForwardRunRepository") as run_repo_cls,
            patch(
                "investing_agent.db.repositories.walkforward.WalkForwardDecisionRepository"
            ) as decision_repo_cls,
        ):
            run_repo_cls.return_value.get = AsyncMock(return_value=run)
            decision_repo_cls.return_value.list_for_run = AsyncMock(return_value=[])

            result = CliRunner().invoke(cli, ["walk-forward-report", "--run-id", str(run.id)])

        assert result.exit_code == 0, result.output
        assert "No decisions found" in result.output
