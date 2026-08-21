from __future__ import annotations

"""Unit tests for the Phase 6C.0 price CLI commands (sync-daily-prices /
sync-benchmark-prices / show-daily-prices) via Click's CliRunner. Mocks the
service/provider entry points and session plumbing — same pattern as
test_cli_broker_history.py."""

from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from investing_agent.cli import cli
from investing_agent.services.prices.ingestion import PriceSyncSummary


class _FakeSessionCM:
    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc) -> bool:
        return False


def _fake_session_factory(session):
    return lambda: _FakeSessionCM(session)


class TestSyncDailyPricesCommand:
    def test_reports_summary_counts(self) -> None:
        session = AsyncMock()
        summary = PriceSyncSummary(dates_checked=64, dates_no_data=3, rows_created=61)

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.prices.nse_bhavcopy.NSEBhavcopyHistoricalPriceProvider"
            ) as provider_cls,
            patch(
                "investing_agent.services.prices.ingestion.sync_daily_prices",
                AsyncMock(return_value=summary),
            ),
        ):
            provider_cls.return_value.aclose = AsyncMock()
            result = CliRunner().invoke(
                cli,
                [
                    "sync-daily-prices", "BEL",
                    "--start-date", "2025-01-01", "--end-date", "2025-03-31",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "dates_checked=64 dates_no_data=3" in result.output
        assert "+61 created, 0 already existed" in result.output

    def test_reports_errors_when_present(self) -> None:
        session = AsyncMock()
        summary = PriceSyncSummary(dates_checked=1, errors=["2025-01-02: blocked — 403"])

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.prices.nse_bhavcopy.NSEBhavcopyHistoricalPriceProvider"
            ) as provider_cls,
            patch(
                "investing_agent.services.prices.ingestion.sync_daily_prices",
                AsyncMock(return_value=summary),
            ),
        ):
            provider_cls.return_value.aclose = AsyncMock()
            result = CliRunner().invoke(
                cli,
                [
                    "sync-daily-prices", "BEL",
                    "--start-date", "2025-01-01", "--end-date", "2025-01-02",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "errors (1):" in result.output
        assert "blocked — 403" in result.output


class TestSyncBenchmarkPricesCommand:
    def test_reports_summary_counts(self) -> None:
        session = AsyncMock()
        summary = PriceSyncSummary(dates_checked=64, dates_no_data=3, rows_created=61)

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.prices.nse_indices.NSEIndicesBenchmarkPriceProvider"
            ) as provider_cls,
            patch(
                "investing_agent.services.prices.ingestion.sync_benchmark_prices",
                AsyncMock(return_value=summary),
            ),
        ):
            provider_cls.return_value.aclose = AsyncMock()
            result = CliRunner().invoke(
                cli,
                [
                    "sync-benchmark-prices", "--benchmark-code", "NIFTY_50",
                    "--start-date", "2025-01-01", "--end-date", "2025-03-31",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "dates_checked=64 dates_no_data=3" in result.output
        assert "+61 created, 0 already existed" in result.output

    def test_rejects_unknown_benchmark_code(self) -> None:
        result = CliRunner().invoke(
            cli,
            [
                "sync-benchmark-prices", "--benchmark-code", "SENSEX",
                "--start-date", "2025-01-01", "--end-date", "2025-03-31",
            ],
        )
        assert result.exit_code != 0
