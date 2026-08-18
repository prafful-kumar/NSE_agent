from __future__ import annotations

"""Unit tests for services/backtesting/runner.py::run_backtest.

CompanyRepository, FinancialResultRepository, FeatureSnapshotRepository,
BacktestScoreRepository, EstimationService, score_estimate, EstimateRunRead
and FinancialResultRead are all patched inside the runner module's
namespace — this proves the orchestration contract (cutoff derivation from
real available_at values, scope preference, period filtering, baseline_pat
extraction, persistence) without touching a real DB. Real end-to-end
behaviour (including PIT correctness of the underlying estimate) is covered
by tests/integration/test_backtesting_integration.py.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from investing_agent.schemas.backtesting import BacktestScoreCreate
from investing_agent.services.backtesting.runner import run_backtest

COMPANY_ID = uuid.uuid4()
PERIOD_ID = uuid.uuid4()
SNAPSHOT_ID = uuid.uuid4()
EARLIEST_AVAILABLE_AT = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def _company(company_id=COMPANY_ID):
    return MagicMock(id=company_id)


def _result_row(period_id=PERIOD_ID, scope="CONSOLIDATED", reporting_basis="QUARTER"):
    row = MagicMock()
    row.period_id = period_id
    row.statement_scope = scope
    row.reporting_basis = reporting_basis
    return row


def _snapshot_row(
    historical_pats: list[str] | None = None,
    *,
    verified_history_count: int | None = None,
    unverified_history_count: int = 0,
):
    historical = [
        {
            "fiscal_year": 2025, "quarter": "Q2", "period_type": "quarter",
            "period_end": "2025-09-30", "statement_scope": "CONSOLIDATED",
            "revenue": "1000", "ebitda_margin_pct": "12", "pat": pat, "eps_diluted": "5",
        }
        for pat in (historical_pats or [])
    ]
    if verified_history_count is None:
        # Default assumes every point in historical_pats is a verified,
        # usable comparable — matches every pre-existing test's intent
        # (they were written before the verified/unverified distinction
        # existed). Tests exercising UNSCORABLE marking pass this
        # explicitly.
        verified_history_count = len(historical)
    row = MagicMock(id=SNAPSHOT_ID)
    row.payload = {
        "target_period": {
            "fiscal_year": 2025, "quarter": "Q3", "period_type": "quarter",
            "period_end": "2025-12-31", "label": "Q3FY25",
        },
        "historical_financials": historical,
        "verified_history_count": verified_history_count,
        "unverified_history_count": unverified_history_count,
    }
    return row


class _Harness:
    """Sets up all the runner-module-level patches shared by every test."""

    def __init__(self, *, results, earliest_available_at=EARLIEST_AVAILABLE_AT, snapshot=None):
        self.results = results
        self.earliest_available_at = earliest_available_at
        self.snapshot = snapshot or _snapshot_row()
        self.generated_run = MagicMock(id=uuid.uuid4(), feature_snapshot_id=self.snapshot.id)
        self.created_score = MagicMock(id=uuid.uuid4())

        self.session = AsyncMock()
        self.session.get = AsyncMock(return_value=MagicMock(label="Q3FY25"))

        self.company_repo = MagicMock()
        self.company_repo.get = AsyncMock(return_value=_company())
        self.company_repo.list = AsyncMock(return_value=[_company()])

        self.result_repo = MagicMock()
        self.result_repo.list_by_company = AsyncMock(return_value=self.results)
        self.result_repo.get_earliest_available_at = AsyncMock(return_value=self.earliest_available_at)

        self.snapshot_repo = MagicMock()
        self.snapshot_repo.get = AsyncMock(return_value=self.snapshot)

        self.score_repo = MagicMock()
        self.score_repo.create = AsyncMock(return_value=self.created_score)

        self.service = MagicMock()
        self.service.generate_estimate = AsyncMock(return_value=self.generated_run)

        # A real BacktestScoreCreate (not a bare MagicMock) so runner.py's
        # score_data.model_copy(update={...}) — where it layers on
        # status/unscorable_reason/verified & unverified history counts —
        # behaves exactly as it does against the real score_estimate().
        self.score_estimate_fn = MagicMock(
            return_value=BacktestScoreCreate(
                company_id=COMPANY_ID,
                financial_period_id=PERIOD_ID,
                estimate_run_id=self.generated_run.id,
                financial_result_id=uuid.uuid4(),
                cutoff_at=EARLIEST_AVAILABLE_AT,
                model_version="deterministic-v1",
            )
        )

    def patches(self):
        return (
            patch("investing_agent.services.backtesting.runner.CompanyRepository", return_value=self.company_repo),
            patch("investing_agent.services.backtesting.runner.FinancialResultRepository", return_value=self.result_repo),
            patch("investing_agent.services.backtesting.runner.FeatureSnapshotRepository", return_value=self.snapshot_repo),
            patch("investing_agent.services.backtesting.runner.BacktestScoreRepository", return_value=self.score_repo),
            patch("investing_agent.services.backtesting.runner.EstimationService", return_value=self.service),
            patch("investing_agent.services.backtesting.runner.score_estimate", self.score_estimate_fn),
            patch("investing_agent.services.backtesting.runner.EstimateRunRead"),
            patch("investing_agent.services.backtesting.runner.FinancialResultRead"),
        )


async def _run(harness: _Harness, **kwargs):
    ctx_managers = harness.patches()
    entered = [cm.__enter__() for cm in ctx_managers]
    # Last two patches are EstimateRunRead / FinancialResultRead classes —
    # keep them accessible for tests that assert on model_validate call args.
    harness.run_read_cls, harness.result_read_cls = entered[-2], entered[-1]
    try:
        return await run_backtest(harness.session, **kwargs)
    finally:
        for cm in reversed(ctx_managers):
            cm.__exit__(None, None, None)


class TestCutoffDerivation:
    async def test_cutoff_is_earliest_available_at_minus_one_second(self) -> None:
        harness = _Harness(results=[_result_row()])
        await _run(harness, model_version="deterministic-v1")

        harness.service.generate_estimate.assert_awaited_once()
        call_kwargs = harness.service.generate_estimate.await_args.kwargs
        assert call_kwargs["cutoff_at"] == EARLIEST_AVAILABLE_AT - timedelta(seconds=1)

    async def test_period_with_no_available_at_is_skipped(self) -> None:
        harness = _Harness(results=[_result_row()], earliest_available_at=None)
        result = await _run(harness)

        harness.service.generate_estimate.assert_not_awaited()
        assert result.scores == []
        assert len(result.diagnostics) == 1
        assert result.diagnostics[0].estimate_status == "SKIPPED"
        assert result.diagnostics[0].reason == "NO_ACTUAL_AVAILABLE_AT"


class TestScopePreference:
    async def test_prefers_consolidated_over_standalone_and_unresolved(self) -> None:
        rows = [
            _result_row(scope="UNRESOLVED"),
            _result_row(scope="STANDALONE"),
            _result_row(scope="CONSOLIDATED"),
        ]
        harness = _Harness(results=rows)
        await _run(harness)

        picked_row = harness.result_read_cls.model_validate.call_args.args[0]
        assert picked_row.statement_scope == "CONSOLIDATED"

    async def test_falls_back_to_standalone_when_no_consolidated(self) -> None:
        rows = [_result_row(scope="UNRESOLVED"), _result_row(scope="STANDALONE")]
        harness = _Harness(results=rows)
        await _run(harness)
        harness.service.generate_estimate.assert_awaited_once()


class TestPeriodFiltering:
    async def test_annual_reporting_basis_rows_are_excluded(self) -> None:
        harness = _Harness(results=[_result_row(reporting_basis="ANNUAL")])
        result = await _run(harness)

        harness.service.generate_estimate.assert_not_awaited()
        assert result.scores == []

    async def test_multiple_periods_each_generate_one_estimate(self) -> None:
        other_period_id = uuid.uuid4()
        rows = [_result_row(period_id=PERIOD_ID), _result_row(period_id=other_period_id)]
        harness = _Harness(results=rows)
        result = await _run(harness)

        assert harness.service.generate_estimate.await_count == 2
        assert len(result.scores) == 2


class TestBaselinePat:
    async def test_baseline_pat_is_latest_historical_points_pat(self) -> None:
        snapshot = _snapshot_row(historical_pats=["80", "90"])
        harness = _Harness(results=[_result_row()], snapshot=snapshot)
        await _run(harness)

        harness.score_estimate_fn.assert_called_once()
        call_kwargs = harness.score_estimate_fn.call_args.kwargs
        assert call_kwargs["baseline_pat"] == 90

    async def test_empty_history_gives_none_baseline(self) -> None:
        snapshot = _snapshot_row(historical_pats=[])
        harness = _Harness(results=[_result_row()], snapshot=snapshot)
        await _run(harness)

        call_kwargs = harness.score_estimate_fn.call_args.kwargs
        assert call_kwargs["baseline_pat"] is None


class TestCompanyScoping:
    async def test_company_id_given_uses_get_not_list(self) -> None:
        harness = _Harness(results=[_result_row()])
        await _run(harness, company_id=COMPANY_ID)

        harness.company_repo.get.assert_awaited_once_with(COMPANY_ID)
        harness.company_repo.list.assert_not_awaited()

    async def test_company_id_none_iterates_all_companies(self) -> None:
        harness = _Harness(results=[_result_row()])
        await _run(harness, company_id=None)

        harness.company_repo.list.assert_awaited_once()
        harness.company_repo.get.assert_not_awaited()


class TestPersistence:
    async def test_each_scored_period_persists_one_backtest_score(self) -> None:
        harness = _Harness(results=[_result_row()])
        result = await _run(harness)

        harness.score_repo.create.assert_awaited_once()
        assert result.scores == [harness.created_score]


class TestUnscorableMarking:
    async def test_no_history_at_all_is_unscorable_no_history_available(self) -> None:
        snapshot = _snapshot_row(verified_history_count=0, unverified_history_count=0)
        harness = _Harness(results=[_result_row()], snapshot=snapshot)
        result = await _run(harness)

        create_kwargs = harness.score_repo.create.call_args.args[0]
        assert create_kwargs.status == "UNSCORABLE"
        assert create_kwargs.unscorable_reason == "NO_HISTORY_AVAILABLE"
        assert result.diagnostics[0].estimate_status == "UNSCORABLE"
        assert result.diagnostics[0].reason == "NO_HISTORY_AVAILABLE"

    async def test_only_unverified_history_is_unscorable_no_verified_history(self) -> None:
        snapshot = _snapshot_row(verified_history_count=0, unverified_history_count=3)
        harness = _Harness(results=[_result_row()], snapshot=snapshot)
        result = await _run(harness)

        create_kwargs = harness.score_repo.create.call_args.args[0]
        assert create_kwargs.status == "UNSCORABLE"
        assert create_kwargs.unscorable_reason == "NO_VERIFIED_HISTORY"
        assert result.diagnostics[0].verified_history_count == 0
        assert result.diagnostics[0].unverified_history_count == 3

    async def test_verified_history_present_is_scored(self) -> None:
        snapshot = _snapshot_row(historical_pats=["80", "90"])
        harness = _Harness(results=[_result_row()], snapshot=snapshot)
        result = await _run(harness)

        create_kwargs = harness.score_repo.create.call_args.args[0]
        assert create_kwargs.status == "SCORED"
        assert create_kwargs.unscorable_reason is None
        assert result.diagnostics[0].estimate_status == "SCORED"
