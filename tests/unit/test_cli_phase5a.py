from __future__ import annotations

"""Unit tests for the Phase 5A CLI commands (generate-estimate,
list-estimates, show-estimate) via Click's CliRunner.

The DB/session layer and EstimationService are mocked throughout, same
pattern as tests/unit/test_cli_phase4b.py — these exercise CLI argument
parsing and output formatting, not real persistence (covered by
tests/integration/test_estimation_service_integration.py).
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from investing_agent.cli import _parse_quarter_arg, cli


class _FakeSessionCM:
    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc) -> bool:
        return False


def _fake_session_factory(session):
    return lambda: _FakeSessionCM(session)


class TestParseQuarterArg:
    def test_two_digit_fiscal_year(self) -> None:
        assert _parse_quarter_arg("Q2FY26") == (2026, "Q2")

    def test_four_digit_fiscal_year(self) -> None:
        assert _parse_quarter_arg("Q4FY2025") == (2025, "Q4")

    def test_lowercase_is_accepted(self) -> None:
        assert _parse_quarter_arg("q1fy27") == (2027, "Q1")

    def test_invalid_format_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Invalid --quarter format"):
            _parse_quarter_arg("bogus")


def _fake_estimate_run(**overrides) -> MagicMock:
    defaults = dict(
        id=uuid.uuid4(), model_version="deterministic-v1",
        cutoff_at="2026-01-15T12:00:00+00:00",
        revenue_low=Decimal("100"), revenue_base=Decimal("110"), revenue_high=Decimal("120"),
        ebitda_margin_low=Decimal("10"), ebitda_margin_base=Decimal("11"), ebitda_margin_high=Decimal("12"),
        pat_low=Decimal("8"), pat_base=Decimal("9"), pat_high=Decimal("10"),
        eps_low=Decimal("1.1"), eps_base=Decimal("1.2"), eps_high=Decimal("1.3"),
        confidence=Decimal("0.6"),
        assumptions=[{"text": "Revenue base case: median YoY growth of 5%.", "source": "deterministic"}],
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


class TestGenerateEstimateCommand:
    def test_bad_quarter_format_exits_before_touching_db(self) -> None:
        result = CliRunner().invoke(cli, ["generate-estimate", "BEL", "--quarter", "bogus"])
        assert result.exit_code == 1
        assert "Invalid --quarter format" in result.output

    def test_no_llm_flag_skips_narrator_construction(self) -> None:
        session = AsyncMock()
        company = SimpleNamespace(id=uuid.uuid4(), symbol="BEL")
        period = SimpleNamespace(id=uuid.uuid4())
        run = _fake_estimate_run()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.ingestion.common.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.db.repositories.financial.FinancialPeriodRepository"
            ) as period_repo_cls,
            patch("investing_agent.services.estimation.service.EstimationService") as service_cls,
            patch(
                "investing_agent.services.estimation.narrator.ClaudeEstimateNarrator"
            ) as narrator_cls,
        ):
            period_repo_cls.return_value.get_or_create = AsyncMock(return_value=period)
            service_cls.return_value.generate_estimate = AsyncMock(return_value=run)

            result = CliRunner().invoke(
                cli, ["generate-estimate", "BEL", "--quarter", "Q2FY26", "--no-llm"]
            )

        assert result.exit_code == 0, result.output
        narrator_cls.assert_not_called()
        assert "deterministic-v1" in result.output
        assert "100 / 110 / 120" in result.output

    def test_missing_api_key_prints_note_and_skips_narrator(self) -> None:
        session = AsyncMock()
        company = SimpleNamespace(id=uuid.uuid4(), symbol="BEL")
        period = SimpleNamespace(id=uuid.uuid4())
        run = _fake_estimate_run()
        fake_settings = SimpleNamespace(
            anthropic_api_key=None, anthropic_model="claude-sonnet-4-6",
            log_level="INFO", is_development=True,
        )

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch("investing_agent.cli.get_settings", return_value=fake_settings),
            patch(
                "investing_agent.services.ingestion.common.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.db.repositories.financial.FinancialPeriodRepository"
            ) as period_repo_cls,
            patch("investing_agent.services.estimation.service.EstimationService") as service_cls,
        ):
            period_repo_cls.return_value.get_or_create = AsyncMock(return_value=period)
            service_cls.return_value.generate_estimate = AsyncMock(return_value=run)

            result = CliRunner().invoke(cli, ["generate-estimate", "BEL", "--quarter", "Q2FY26"])

        assert result.exit_code == 0, result.output
        assert "ANTHROPIC_API_KEY not set" in result.output

    def test_service_exception_rolls_back_and_exits_nonzero(self) -> None:
        session = AsyncMock()
        company = SimpleNamespace(id=uuid.uuid4(), symbol="BEL")
        period = SimpleNamespace(id=uuid.uuid4())

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.ingestion.common.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.db.repositories.financial.FinancialPeriodRepository"
            ) as period_repo_cls,
            patch("investing_agent.services.estimation.service.EstimationService") as service_cls,
        ):
            period_repo_cls.return_value.get_or_create = AsyncMock(return_value=period)
            service_cls.return_value.generate_estimate = AsyncMock(
                side_effect=RuntimeError("boom")
            )

            result = CliRunner().invoke(
                cli, ["generate-estimate", "BEL", "--quarter", "Q2FY26", "--no-llm"]
            )

        assert result.exit_code == 1
        assert "ERROR: boom" in result.output
        session.rollback.assert_awaited()


class TestListEstimatesCommand:
    def test_lists_runs_with_period_labels(self) -> None:
        session = AsyncMock()
        company = SimpleNamespace(id=uuid.uuid4(), symbol="BEL")
        run = _fake_estimate_run()
        period = SimpleNamespace(label="Q2FY26")

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.ingestion.common.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.db.repositories.estimation.EstimateRunRepository"
            ) as run_repo_cls,
            patch(
                "investing_agent.db.repositories.financial.FinancialPeriodRepository"
            ) as period_repo_cls,
        ):
            run_repo_cls.return_value.list_by_company = AsyncMock(return_value=[run])
            period_repo_cls.return_value.get = AsyncMock(return_value=period)

            result = CliRunner().invoke(cli, ["list-estimates", "BEL"])

        assert result.exit_code == 0, result.output
        assert "Q2FY26" in result.output
        assert "rev=110" in result.output


class TestShowEstimateCommand:
    def test_shows_full_detail_including_assumptions(self) -> None:
        session = AsyncMock()
        run = _fake_estimate_run()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.estimation.EstimateRunRepository"
            ) as run_repo_cls,
        ):
            run_repo_cls.return_value.get = AsyncMock(return_value=run)
            result = CliRunner().invoke(cli, ["show-estimate", str(run.id)])

        assert result.exit_code == 0, result.output
        assert "[deterministic] Revenue base case" in result.output

    def test_missing_estimate_exits_nonzero(self) -> None:
        session = AsyncMock()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.estimation.EstimateRunRepository"
            ) as run_repo_cls,
        ):
            run_repo_cls.return_value.get = AsyncMock(return_value=None)
            result = CliRunner().invoke(cli, ["show-estimate", str(uuid.uuid4())])

        assert result.exit_code == 1
        assert "No EstimateRun found" in result.output
