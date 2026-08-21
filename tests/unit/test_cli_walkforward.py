from __future__ import annotations

"""Unit tests for the Phase 6C CLI commands (walk-forward-run,
walk-forward-show) via Click's CliRunner. Mirrors test_cli_phase5b.py's
pattern: mock the service/repository entry points, let the CLI's own
parsing/formatting run for real."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from investing_agent.cli import cli
from investing_agent.services.walkforward.runner import WalkForwardEntry, WalkForwardRunResult

NOW = datetime(2026, 1, 1, tzinfo=UTC)


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


class TestWalkForwardRunCommand:
    def test_no_account_found_exits_nonzero(self) -> None:
        session = AsyncMock()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.db.repositories.broker_history.BrokerAccountRepository") as repo_cls,
        ):
            repo_cls.return_value.get_by_label = AsyncMock(return_value=None)
            result = CliRunner().invoke(
                cli,
                [
                    "walk-forward-run", "--broker", "ZERODHA", "--account-label", "primary",
                    "--position", "BEL:2021-06-15",
                ],
            )

        assert result.exit_code == 1
        assert "ERROR: no BrokerAccount" in result.output

    def test_invalid_position_format_exits_nonzero(self) -> None:
        result = CliRunner().invoke(
            cli,
            [
                "walk-forward-run", "--broker", "ZERODHA", "--account-label", "primary",
                "--position", "not-a-valid-position",
            ],
        )
        assert result.exit_code == 1
        assert "invalid --position" in result.output

    def test_valid_run_parses_positions_and_prints_summary(self) -> None:
        session = AsyncMock()
        account = SimpleNamespace(id=uuid.uuid4(), strategy_profile="LONG_TERM")
        run = SimpleNamespace(id=uuid.uuid4())
        entry = WalkForwardEntry(decision=_fake_decision(), outcome=_fake_outcome())
        run_result = WalkForwardRunResult(run=run, entries=[entry])

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.db.repositories.broker_history.BrokerAccountRepository") as repo_cls,
            patch(
                "investing_agent.services.walkforward.runner.run_walk_forward",
                AsyncMock(return_value=run_result),
            ) as run_fn,
        ):
            repo_cls.return_value.get_by_label = AsyncMock(return_value=account)
            result = CliRunner().invoke(
                cli,
                [
                    "walk-forward-run", "--broker", "ZERODHA", "--account-label", "primary",
                    "--position", "BEL:2021-06-15", "--position", "TCS:2022-01-10",
                    "--horizons", "1,3", "--model-version", "walkforward-v2",
                ],
            )

        assert result.exit_code == 0, result.output
        run_fn.assert_awaited_once()
        call_kwargs = run_fn.await_args.kwargs
        assert call_kwargs["broker_account_id"] == account.id
        assert call_kwargs["symbol_dates"] == [
            ("BEL", date(2021, 6, 15)), ("TCS", date(2022, 1, 10)),
        ]
        assert call_kwargs["horizons_months"] == (1, 3)
        assert call_kwargs["model_version"] == "walkforward-v2"
        assert "1 decisions frozen+scored" in result.output
        assert "BEL" in result.output

    def test_runner_exception_rolls_back_and_exits_nonzero(self) -> None:
        session = AsyncMock()
        account = SimpleNamespace(id=uuid.uuid4(), strategy_profile="LONG_TERM")

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.db.repositories.broker_history.BrokerAccountRepository") as repo_cls,
            patch(
                "investing_agent.services.walkforward.runner.run_walk_forward",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            repo_cls.return_value.get_by_label = AsyncMock(return_value=account)
            result = CliRunner().invoke(
                cli,
                [
                    "walk-forward-run", "--broker", "ZERODHA", "--account-label", "primary",
                    "--position", "BEL:2021-06-15",
                ],
            )

        assert result.exit_code == 1
        assert "ERROR: boom" in result.output
        session.rollback.assert_awaited()


class TestWalkForwardShowCommand:
    def test_no_run_found_exits_nonzero(self) -> None:
        session = AsyncMock()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.db.repositories.walkforward.WalkForwardRunRepository") as run_repo_cls,
        ):
            run_repo_cls.return_value.get = AsyncMock(return_value=None)
            result = CliRunner().invoke(cli, ["walk-forward-show", "--run-id", str(uuid.uuid4())])

        assert result.exit_code == 1
        assert "ERROR: no WalkForwardRun" in result.output

    def test_shows_actual_and_hold_baseline_with_returns(self) -> None:
        session = AsyncMock()
        run = SimpleNamespace(
            id=uuid.uuid4(), broker_account_id=uuid.uuid4(), strategy_profile="LONG_TERM",
            horizons_months=[1, 3, 6, 12], model_version="walkforward-v1",
        )
        actual_decision = _fake_decision(decision_source="ACTUAL", action="BUY")
        hold_decision = _fake_decision(
            id=uuid.uuid4(), decision_source="HOLD_BASELINE", action="HOLD",
        )
        outcome = _fake_outcome()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.db.repositories.walkforward.WalkForwardRunRepository") as run_repo_cls,
            patch(
                "investing_agent.db.repositories.walkforward.WalkForwardDecisionRepository"
            ) as decision_repo_cls,
            patch(
                "investing_agent.db.repositories.walkforward.WalkForwardOutcomeRepository"
            ) as outcome_repo_cls,
        ):
            run_repo_cls.return_value.get = AsyncMock(return_value=run)
            decision_repo_cls.return_value.list_for_run = AsyncMock(
                return_value=[actual_decision, hold_decision]
            )
            outcome_repo_cls.return_value.get_by_decision_id = AsyncMock(return_value=outcome)

            result = CliRunner().invoke(cli, ["walk-forward-show", "--run-id", str(run.id)])

        assert result.exit_code == 0, result.output
        assert "BEL" in result.output
        assert "ACTUAL" in result.output
        assert "HOLD_BASELINE" in result.output
        assert "not available (no deterministic recommendation generator in v1)" in result.output
        assert "1M: stock=0.05" in result.output
