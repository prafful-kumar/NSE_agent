from __future__ import annotations

"""Unit tests for the Phase 6B portfolio-reconstruction CLI commands
(reconstruct-portfolio / rebuild-position-lots / reconcile-portfolio) via
Click's CliRunner. Mocks the service entry points and session plumbing --
same pattern as test_cli_broker_history.py."""

import uuid
from decimal import Decimal
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


def _snapshot(**overrides):
    defaults = dict(
        broker_account_id=uuid.uuid4(),
        as_of_date="2026-08-19",
        strategy_profile="LONG_TERM",
        positions=[],
        cash_balance_partial=Decimal("100"),
        cash_balance_caveat="some caveat",
        cash_balance_ledger_coverage_start=None,
        invested_capital_total=Decimal("0"),
        unrealized_pnl_total=None,
        realized_pnl_cumulative_total=Decimal("0"),
        warnings=[],
        corporate_action_adjustments=[],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestReconstructPortfolioCommand:
    def test_missing_account_exits(self) -> None:
        session = AsyncMock()
        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.broker_history.BrokerAccountRepository"
            ) as repo_cls,
        ):
            repo_cls.return_value.get_by_label = AsyncMock(return_value=None)
            result = CliRunner().invoke(
                cli, ["reconstruct-portfolio", "--broker", "ZERODHA", "--account-label", "primary"]
            )
        assert result.exit_code == 1
        assert "ERROR" in result.output

    def test_prints_snapshot(self) -> None:
        session = AsyncMock()
        account = SimpleNamespace(id=uuid.uuid4())
        position = SimpleNamespace(
            symbol="INFY", quantity_held=Decimal("10"), average_cost=Decimal("1000"),
            invested_capital=Decimal("10000"), realized_pnl_cumulative=Decimal("500"),
            corporate_action_flag=False,
        )
        snapshot = _snapshot(positions=[position])

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.broker_history.BrokerAccountRepository"
            ) as repo_cls,
            patch(
                "investing_agent.services.reconstruction.service.get_portfolio_as_of",
                AsyncMock(return_value=snapshot),
            ),
        ):
            repo_cls.return_value.get_by_label = AsyncMock(return_value=account)
            result = CliRunner().invoke(
                cli,
                [
                    "reconstruct-portfolio", "--broker", "ZERODHA",
                    "--account-label", "primary", "--as-of", "2026-08-19",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "INFY" in result.output
        assert "LONG_TERM" in result.output


class TestRebuildPositionLotsCommand:
    def test_missing_account_exits(self) -> None:
        session = AsyncMock()
        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.broker_history.BrokerAccountRepository"
            ) as repo_cls,
        ):
            repo_cls.return_value.get_by_label = AsyncMock(return_value=None)
            result = CliRunner().invoke(
                cli, ["rebuild-position-lots", "--broker", "ZERODHA", "--account-label", "primary"]
            )
        assert result.exit_code == 1

    def test_reports_counts(self) -> None:
        session = AsyncMock()
        account = SimpleNamespace(id=uuid.uuid4())
        run_result = SimpleNamespace(
            as_of_date="2026-08-19", lots_written=42, symbols_with_open_positions=10, warnings=[]
        )

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.broker_history.BrokerAccountRepository"
            ) as repo_cls,
            patch(
                "investing_agent.services.reconstruction.service.reconstruct_and_persist",
                AsyncMock(return_value=run_result),
            ),
        ):
            repo_cls.return_value.get_by_label = AsyncMock(return_value=account)
            result = CliRunner().invoke(
                cli, ["rebuild-position-lots", "--broker", "ZERODHA", "--account-label", "primary"]
            )

        assert result.exit_code == 0, result.output
        assert "lots_written=42" in result.output
        session.commit.assert_awaited()


class TestReconcilePortfolioCommand:
    def test_clean_report_exits_zero(self, tmp_path) -> None:
        session = AsyncMock()
        account = SimpleNamespace(id=uuid.uuid4())
        quality_score = SimpleNamespace(
            holdings_quantity_match_pct=100.0, holdings_avg_cost_match_pct=100.0,
            realized_pnl_match_pct=100.0, unresolved_symbol_count=0,
        )
        report = SimpleNamespace(
            overall_status="CLEAN", total_symbols_checked=5, divergent_symbols=[],
            expected_gaps_corporate_action=[], row_diffs=[], warnings=[],
            quality_score=quality_score,
        )

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.broker_history.BrokerAccountRepository"
            ) as repo_cls,
            patch(
                "investing_agent.services.reconstruction.reconciliation.reconcile_account",
                AsyncMock(return_value=report),
            ),
        ):
            repo_cls.return_value.get_by_label = AsyncMock(return_value=account)
            result = CliRunner().invoke(
                cli,
                [
                    "reconcile-portfolio", "--broker", "ZERODHA",
                    "--account-label", "primary", "--statements-dir", str(tmp_path),
                ],
            )

        assert result.exit_code == 0, result.output
        assert "CLEAN" in result.output

    def test_divergent_report_exits_nonzero(self, tmp_path) -> None:
        session = AsyncMock()
        account = SimpleNamespace(id=uuid.uuid4())
        diff = SimpleNamespace(
            statement_file="pnl-x.xlsx", symbol="XYZ", field="realized_pnl",
            expected=Decimal("100"), actual=Decimal("0"), delta=Decimal("-100"),
            likely_cause="untracked corporate action",
        )
        quality_score = SimpleNamespace(
            holdings_quantity_match_pct=None, holdings_avg_cost_match_pct=None,
            realized_pnl_match_pct=80.0, unresolved_symbol_count=1,
        )
        report = SimpleNamespace(
            overall_status="DIVERGENT", total_symbols_checked=5, divergent_symbols=["XYZ"],
            expected_gaps_corporate_action=[], row_diffs=[diff], warnings=[],
            quality_score=quality_score,
        )

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.broker_history.BrokerAccountRepository"
            ) as repo_cls,
            patch(
                "investing_agent.services.reconstruction.reconciliation.reconcile_account",
                AsyncMock(return_value=report),
            ),
        ):
            repo_cls.return_value.get_by_label = AsyncMock(return_value=account)
            result = CliRunner().invoke(
                cli,
                [
                    "reconcile-portfolio", "--broker", "ZERODHA",
                    "--account-label", "primary", "--statements-dir", str(tmp_path),
                ],
            )

        assert result.exit_code == 1
        assert "DIVERGENT" in result.output
        assert "XYZ" in result.output


class TestRecordCorporateActionCommand:
    def test_records_split(self) -> None:
        session = AsyncMock()
        row = SimpleNamespace(id=uuid.uuid4(), version=1)

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.reconstruction.corporate_actions.record_corporate_action_event",
                AsyncMock(return_value=(row, True)),
            ),
        ):
            result = CliRunner().invoke(
                cli,
                [
                    "record-corporate-action", "ANGELONE",
                    "--event-type", "SPLIT", "--event-date", "2026-02-26",
                    "--ratio-old", "1", "--ratio-new", "10",
                    "--source", "NSE record date circular (web-verified)",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "ANGELONE" in result.output
        assert "Recorded" in result.output
        session.commit.assert_awaited()


class TestRecordOpeningPositionAdjustmentCommand:
    def test_missing_account_exits(self) -> None:
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
                [
                    "record-opening-position-adjustment", "--broker", "ZERODHA",
                    "--account-label", "primary", "ZAGGLE",
                    "--opening-date", "2026-08-13", "--quantity", "40", "--cost-price", "458.00",
                    "--source", "ZERODHA_PNL_RECONCILIATION", "--confidence", "MEDIUM",
                    "--reason", "MISSING_TRADE_HISTORY",
                ],
            )
        assert result.exit_code == 1

    def test_records_adjustment(self) -> None:
        session = AsyncMock()
        account = SimpleNamespace(id=uuid.uuid4())
        row = SimpleNamespace(id=uuid.uuid4())

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.broker_history.BrokerAccountRepository"
            ) as repo_cls,
            patch(
                "investing_agent.services.reconstruction.corporate_actions."
                "record_opening_position_adjustment",
                AsyncMock(return_value=(row, True)),
            ),
        ):
            repo_cls.return_value.get_by_label = AsyncMock(return_value=account)
            result = CliRunner().invoke(
                cli,
                [
                    "record-opening-position-adjustment", "--broker", "ZERODHA",
                    "--account-label", "primary", "ZAGGLE",
                    "--opening-date", "2026-08-13", "--quantity", "40", "--cost-price", "458.00",
                    "--source", "ZERODHA_PNL_RECONCILIATION", "--confidence", "MEDIUM",
                    "--reason", "MISSING_TRADE_HISTORY",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "ZAGGLE" in result.output
        session.commit.assert_awaited()
