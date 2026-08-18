from __future__ import annotations

"""Unit tests for the Phase 5B CLI commands (run-backtest, backtest-report)
via Click's CliRunner.

run-backtest tests mock services.backtesting.runner.run_backtest itself
(same "mock the orchestration entry point" pattern as test_cli_phase5a.py).
backtest-report tests mock only the repositories and let the real
summarize/group_and_summarize aggregation run over hand-built rows, proving
the CLI wires filtering + real aggregation + output formatting correctly.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from investing_agent.cli import cli
from investing_agent.services.backtesting.runner import BacktestRunResult

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


def _fake_score(**overrides) -> MagicMock:
    defaults = dict(
        id=uuid.uuid4(), financial_period_id=uuid.uuid4(),
        revenue_error_pct=Decimal("2.5"), pat_error_pct=Decimal("-3.1"), eps_error_pct=Decimal("1.0"),
        surprise_direction="inline", within_band_pat=True, confidence_bucket="medium",
        status="SCORED",
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


class TestRunBacktestCommand:
    def test_symbol_given_resolves_company_and_scopes_run(self) -> None:
        session = AsyncMock()
        company = SimpleNamespace(id=uuid.uuid4(), symbol="BEL")
        run_result = BacktestRunResult(scores=[_fake_score()], diagnostics=[])

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.ingestion.common.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.services.backtesting.runner.run_backtest",
                AsyncMock(return_value=run_result),
            ) as run_backtest_fn,
        ):
            result = CliRunner().invoke(cli, ["run-backtest", "--symbol", "BEL"])

        assert result.exit_code == 0, result.output
        run_backtest_fn.assert_awaited_once()
        call_kwargs = run_backtest_fn.await_args.kwargs
        assert call_kwargs["company_id"] == company.id
        assert call_kwargs["model_version"] == "deterministic-v1"
        assert "1 scores persisted" in result.output

    def test_no_symbol_runs_across_all_companies(self) -> None:
        session = AsyncMock()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.backtesting.runner.run_backtest",
                AsyncMock(return_value=BacktestRunResult()),
            ) as run_backtest_fn,
        ):
            result = CliRunner().invoke(cli, ["run-backtest"])

        assert result.exit_code == 0, result.output
        call_kwargs = run_backtest_fn.await_args.kwargs
        assert call_kwargs["company_id"] is None

    def test_custom_model_version_is_passed_through(self) -> None:
        session = AsyncMock()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.backtesting.runner.run_backtest",
                AsyncMock(return_value=BacktestRunResult()),
            ) as run_backtest_fn,
        ):
            result = CliRunner().invoke(
                cli, ["run-backtest", "--model-version", "deterministic-v2"]
            )

        assert result.exit_code == 0, result.output
        assert run_backtest_fn.await_args.kwargs["model_version"] == "deterministic-v2"

    def test_runner_exception_rolls_back_and_exits_nonzero(self) -> None:
        session = AsyncMock()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.backtesting.runner.run_backtest",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            result = CliRunner().invoke(cli, ["run-backtest"])

        assert result.exit_code == 1
        assert "ERROR: boom" in result.output
        session.rollback.assert_awaited()


def _score_row(
    *,
    company_id, financial_period_id=None, model_version="deterministic-v1",
    pat_error_pct=None, within_band_pat=None, surprise_direction=None,
    growth_direction_correct=None, confidence_bucket=None,
    status="SCORED", unscorable_reason=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), company_id=company_id,
        financial_period_id=financial_period_id or uuid.uuid4(),
        estimate_run_id=uuid.uuid4(), financial_result_id=uuid.uuid4(), cutoff_at=NOW,
        model_version=model_version,
        revenue_error_pct=None, pat_error_pct=pat_error_pct, eps_error_pct=None,
        margin_error_bps=None,
        within_band_revenue=None, within_band_pat=within_band_pat, within_band_eps=None,
        surprise_direction=surprise_direction, growth_direction_correct=growth_direction_correct,
        confidence_bucket=confidence_bucket,
        status=status, unscorable_reason=unscorable_reason,
        verified_history_count=2, unverified_history_count=0,
        created_at=NOW, updated_at=NOW,
    )


class TestBacktestReportCommand:
    def test_no_scores_prints_no_matches_message(self) -> None:
        session = AsyncMock()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.db.repositories.company.CompanyRepository") as company_repo_cls,
            patch(
                "investing_agent.db.repositories.backtesting.BacktestScoreRepository"
            ) as score_repo_cls,
        ):
            company_repo_cls.return_value.list = AsyncMock(return_value=[])
            score_repo_cls.return_value.list_all = AsyncMock(return_value=[])

            result = CliRunner().invoke(cli, ["backtest-report"])

        assert result.exit_code == 0, result.output
        assert "No backtest scores found" in result.output

    def test_aggregates_and_prints_all_sections(self) -> None:
        session = AsyncMock()
        company = SimpleNamespace(id=uuid.uuid4(), symbol="BEL", sector="Defence")
        rows = [
            _score_row(
                company_id=company.id, pat_error_pct=Decimal("10"), within_band_pat=True,
                surprise_direction="beat", growth_direction_correct=True, confidence_bucket="high",
            ),
            _score_row(
                company_id=company.id, pat_error_pct=Decimal("-20"), within_band_pat=False,
                surprise_direction="miss", growth_direction_correct=False, confidence_bucket="low",
            ),
        ]

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.db.repositories.company.CompanyRepository") as company_repo_cls,
            patch(
                "investing_agent.db.repositories.backtesting.BacktestScoreRepository"
            ) as score_repo_cls,
        ):
            company_repo_cls.return_value.list = AsyncMock(return_value=[company])
            score_repo_cls.return_value.list_all = AsyncMock(return_value=rows)

            result = CliRunner().invoke(cli, ["backtest-report"])

        assert result.exit_code == 0, result.output
        assert "Backtest report — 2 scores" in result.output
        assert "PAT MAPE" in result.output
        assert "Confidence calibration:" in result.output
        assert "By company:" in result.output
        assert "BEL" in result.output
        assert "By sector:" in result.output
        assert "Defence" in result.output
        assert "By model version:" in result.output
        assert "deterministic-v1" in result.output

    def test_sector_filter_excludes_other_sectors(self) -> None:
        session = AsyncMock()
        defence_co = SimpleNamespace(id=uuid.uuid4(), symbol="BEL", sector="Defence")
        it_co = SimpleNamespace(id=uuid.uuid4(), symbol="TCS", sector="IT")
        rows = [
            _score_row(company_id=defence_co.id, pat_error_pct=Decimal("5")),
            _score_row(company_id=it_co.id, pat_error_pct=Decimal("50")),
        ]

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.db.repositories.company.CompanyRepository") as company_repo_cls,
            patch(
                "investing_agent.db.repositories.backtesting.BacktestScoreRepository"
            ) as score_repo_cls,
        ):
            company_repo_cls.return_value.list = AsyncMock(return_value=[defence_co, it_co])
            score_repo_cls.return_value.list_all = AsyncMock(return_value=rows)

            result = CliRunner().invoke(cli, ["backtest-report", "--sector", "Defence"])

        assert result.exit_code == 0, result.output
        assert "Backtest report — 1 scores" in result.output

    def test_symbol_filter_resolves_company_and_scopes_scores(self) -> None:
        session = AsyncMock()
        company = SimpleNamespace(id=uuid.uuid4(), symbol="BEL", sector="Defence")
        other = SimpleNamespace(id=uuid.uuid4(), symbol="HAL", sector="Defence")
        rows = [
            _score_row(company_id=company.id, pat_error_pct=Decimal("5")),
            _score_row(company_id=other.id, pat_error_pct=Decimal("50")),
        ]

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.ingestion.common.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch("investing_agent.db.repositories.company.CompanyRepository") as company_repo_cls,
            patch(
                "investing_agent.db.repositories.backtesting.BacktestScoreRepository"
            ) as score_repo_cls,
        ):
            company_repo_cls.return_value.list = AsyncMock(return_value=[company, other])
            score_repo_cls.return_value.list_all = AsyncMock(return_value=rows)

            result = CliRunner().invoke(cli, ["backtest-report", "--symbol", "BEL"])

        assert result.exit_code == 0, result.output
        assert "Backtest report — 1 scores" in result.output
