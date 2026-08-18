from __future__ import annotations

"""Integration tests for services/backtesting/runner.py::run_backtest against
a real PostgreSQL database.

Two scenarios are covered separately because of a real Postgres behaviour
worth calling out: within a single uncommitted transaction, now() (used by
FinancialResult.available_at's server_default) is frozen at transaction
start -- it does not advance between statements (clock_timestamp() does,
now()/CURRENT_TIMESTAMP does not). So:

- TestRunBacktestWithinASingleTransaction uses the normal rollback-per-test
  db_session fixture. Every seeded row shares one frozen available_at, so
  run_backtest's cutoff_at (that period's earliest available_at minus one
  second) ends up strictly before every row's available_at, including the
  supposed historical comparables -- every backtest in this test correctly,
  deterministically produces an all-None estimate. This is a real, useful
  edge case to prove: the cutoff is strict (< not <=) and the runner doesn't
  choke on "no data at all", it produces a fully-None BacktestScore instead
  of crashing or fabricating a number.

- TestRunBacktestWithGenuineHistoricalSeparation commits historical quarters
  in one transaction, sleeps just over a second (a real wall-clock gap is
  the only way to get Postgres's now() to advance), then commits the target
  quarter's own actual in a second transaction. This reproduces real
  production spacing (quarters are reported months apart) and is what
  proves the "meaningful, non-degenerate" path: a real populated estimate,
  scored against the real actual, with the target's own actual provably
  excluded from its own comparables. It uses AsyncSessionLocal() directly
  (not the rollback fixture) and a randomized company symbol, so the
  committed rows are harmless leftovers scoped to that one throwaway company.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d
"""

import asyncio
import statistics
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.estimation import FeatureSnapshotPayload
from investing_agent.schemas.financials import FinancialResultCreate
from investing_agent.services.backtesting.runner import run_backtest

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session():
    import os

    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://investing:investing@localhost:5433/investing_agent_test"
    )
    from investing_agent.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def company(db_session):
    from investing_agent.db.repositories.company import CompanyRepository

    repo = CompanyRepository(db_session)
    return await repo.upsert(
        CompanyCreate(
            symbol=f"BEL-{uuid.uuid4().hex[:8]}", name="Bharat Electronics",
            exchange="NSE", sector="Defence",
        )
    )


async def _seed_quarter(
    session, company, fiscal_year, quarter, period_end, label, revenue,
    pat=None, eps_diluted=None, verified=False,
):
    from investing_agent.db.repositories.financial import (
        FinancialPeriodRepository,
        FinancialResultRepository,
    )

    period = await FinancialPeriodRepository(session).get_or_create(
        company.id, "quarter", fiscal_year, quarter, period_end, label
    )
    await FinancialResultRepository(session).upsert_versioned(
        FinancialResultCreate(
            period_id=period.id, company_id=company.id, symbol=company.symbol,
            statement_scope="CONSOLIDATED", reporting_basis="QUARTER", is_audited=False,
            result_date=period_end, currency="INR", unit_scale="CRORE",
            revenue=Decimal(str(revenue)),
            ebitda=None, ebitda_margin_pct=None, ebitda_source=None, pbt=None,
            pat=Decimal(str(pat)) if pat is not None else None,
            pat_margin_pct=None, eps_basic=None,
            eps_diluted=Decimal(str(eps_diluted)) if eps_diluted is not None else None,
            total_debt=None, cash_equivalents=None, operating_cash_flow=None,
            roe_pct=None, roce_pct=None, other_metrics={},
            source_type="test", source_url=None,
            published_at=datetime.now(UTC), data_category="fact", confidence=None,
            source_document_id=None,
            verification_status="verified" if verified else "unverified",
        )
    )
    return period


class TestRunBacktestWithinASingleTransaction:
    async def test_produces_one_all_none_score_per_period_with_no_visible_history(
        self, db_session, company
    ) -> None:
        periods = {}
        for fy, q, pe, label, revenue in [
            (2024, "Q4", date(2024, 3, 31), "Q4FY24", 950),
            (2025, "Q1", date(2024, 6, 30), "Q1FY25", 1000),
            (2025, "Q2", date(2024, 9, 30), "Q2FY25", 1050),
            (2025, "Q3", date(2024, 12, 31), "Q3FY25", 1100),
        ]:
            periods[label] = await _seed_quarter(db_session, company, fy, q, pe, label, revenue)
        await db_session.flush()

        result = await run_backtest(db_session, company_id=company.id)
        await db_session.flush()

        assert len(result.scores) == 4
        period_ids = {p.id for p in periods.values()}
        assert {s.financial_period_id for s in result.scores} == period_ids

        for s in result.scores:
            assert s.model_version == "deterministic-v1"
            # available_at is frozen for the whole transaction (Postgres
            # now() semantics) -- cutoff_at (earliest available_at minus one
            # second) ends up strictly before every row's available_at, so
            # every estimate -- including the target's own quarter -- has no
            # visible history and every numeric/derived field is None.
            assert s.revenue_error_pct is None
            assert s.pat_error_pct is None
            assert s.eps_error_pct is None
            assert s.within_band_revenue is None
            assert s.within_band_pat is None
            assert s.surprise_direction is None
            assert s.growth_direction_correct is None
            # Confidence is still Python-computed (never None) even with
            # zero history -- see deterministic.py's confidence floor.
            assert s.confidence_bucket == "low"
            # No history at all was visible (not even an unverified row) --
            # an explicit UNSCORABLE marker, not a misleadingly "successful"
            # all-None score.
            assert s.status == "UNSCORABLE"
            assert s.unscorable_reason == "NO_HISTORY_AVAILABLE"

        for d in result.diagnostics:
            assert d.estimate_status == "UNSCORABLE"
            assert d.reason == "NO_HISTORY_AVAILABLE"


class TestRunBacktestWithGenuineHistoricalSeparation:
    async def test_scores_a_real_populated_estimate_against_the_actual_without_leakage(self) -> None:
        from investing_agent.db.repositories.company import CompanyRepository
        from investing_agent.db.repositories.estimation import EstimateRunRepository, FeatureSnapshotRepository
        from investing_agent.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            company = await CompanyRepository(session).upsert(
                CompanyCreate(
                    symbol=f"HAL-{uuid.uuid4().hex[:8]}", name="Hindustan Aeronautics",
                    exchange="NSE", sector="Defence",
                )
            )
            await _seed_quarter(
                session, company, 2024, "Q4", date(2024, 3, 31), "Q4FY24", 950, verified=True
            )
            await _seed_quarter(
                session, company, 2025, "Q1", date(2024, 6, 30), "Q1FY25", 1000, verified=True
            )
            await _seed_quarter(
                session, company, 2025, "Q2", date(2024, 9, 30), "Q2FY25", 1050, verified=True
            )
            await session.commit()

        # A genuine wall-clock gap is the only way to get a later now() in a
        # fresh transaction -- sleeping inside one transaction does nothing,
        # since now() is fixed at transaction start (verified empirically).
        await asyncio.sleep(1.1)

        async with AsyncSessionLocal() as session:
            target_period = await _seed_quarter(
                session, company, 2025, "Q3", date(2024, 12, 31), "Q3FY25", 1100
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            result = await run_backtest(session, company_id=company.id)
            await session.commit()

        target_score = next(s for s in result.scores if s.financial_period_id == target_period.id)

        samples = [
            (Decimal("1000") - Decimal("950")) / Decimal("950"),
            (Decimal("1050") - Decimal("1000")) / Decimal("1000"),
        ]
        median_growth = Decimal(str(statistics.median(samples)))
        expected_base = (Decimal("1050") * (1 + median_growth)).quantize(Decimal("0.01"))
        expected_error_pct = (
            ((Decimal("1100") - expected_base) / abs(expected_base)) * 100
        ).quantize(Decimal("0.0001"))

        assert target_score.revenue_error_pct == expected_error_pct
        assert target_score.within_band_revenue is not None
        assert target_score.status == "SCORED"
        assert target_score.unscorable_reason is None
        assert target_score.verified_history_count == 3

        async with AsyncSessionLocal() as session:
            run = await EstimateRunRepository(session).get(target_score.estimate_run_id)
            snapshot_row = await FeatureSnapshotRepository(session).get(run.feature_snapshot_id)
            payload = FeatureSnapshotPayload(**snapshot_row.payload)

        seen_period_ends = {p.period_end for p in payload.historical_financials}
        seen_revenues = {p.revenue for p in payload.historical_financials}
        assert date(2024, 12, 31) not in seen_period_ends
        assert Decimal("1100") not in seen_revenues
        assert len(payload.historical_financials) == 3
