from __future__ import annotations

"""Integration test for EstimationService.generate_estimate against a real
PostgreSQL database — proves the full FinancialResult -> FeatureSnapshot ->
DeterministicEstimate -> EstimateRun persistence path, snapshot
idempotency, the narrator's numeric-safety guarantee end-to-end, and —
critically — the point-in-time leakage guard: a target period's own actual
FinancialResult already sitting in the DB (e.g. because a backtest is being
run for a quarter whose real results have since been ingested) must never
leak into the historical comparables used to produce its own estimate.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d

Note: FinancialResultCreate has no available_at field — it is always set
server-side via server_default=func.now() at INSERT time, so PIT tests here
rely on real insertion-order timing (capture cutoff_at *after* the rows
that should be visible, *before* any that shouldn't), not backdating.
"""

import uuid
from datetime import UTC, date, datetime

import pytest

from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.estimation import FeatureSnapshotPayload
from investing_agent.schemas.financials import FinancialResultCreate
from investing_agent.services.estimation.deterministic import DeterministicEstimate
from investing_agent.services.estimation.narrator import EstimateNarrator, NarrativeCandidate
from investing_agent.services.estimation.service import EstimationService

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
            symbol=f"BEL-{uuid.uuid4().hex[:8]}", name="Bharat Electronics", exchange="NSE"
        )
    )


async def _make_quarter_result(
    db_session, company, fiscal_year, quarter, period_end, label, revenue, pat=None, eps_diluted=None
):
    from investing_agent.db.repositories.financial import (
        FinancialPeriodRepository,
        FinancialResultRepository,
    )
    from decimal import Decimal

    period_repo = FinancialPeriodRepository(db_session)
    period = await period_repo.get_or_create(
        company.id, "quarter", fiscal_year, quarter, period_end, label
    )
    result_repo = FinancialResultRepository(db_session)
    await result_repo.upsert_versioned(
        FinancialResultCreate(
            period_id=period.id, company_id=company.id, symbol=company.symbol,
            statement_scope="CONSOLIDATED", reporting_basis="QUARTER", is_audited=False,
            result_date=period_end, currency="INR", unit_scale="CRORE",
            revenue=Decimal(str(revenue)),
            ebitda=None, ebitda_margin_pct=None, ebitda_source=None, pbt=None,
            pat=Decimal(str(pat)) if pat is not None else None,
            pat_margin_pct=None,
            eps_basic=None,
            eps_diluted=Decimal(str(eps_diluted)) if eps_diluted is not None else None,
            total_debt=None, cash_equivalents=None, operating_cash_flow=None,
            roe_pct=None, roce_pct=None, other_metrics={},
            source_type="test", source_url=None,
            published_at=datetime.now(UTC), data_category="fact", confidence=None,
            source_document_id=None,
        )
    )
    await db_session.flush()
    return period


class FakeNarrator(EstimateNarrator):
    def __init__(self) -> None:
        self.calls = 0

    async def narrate(self, *, computed: DeterministicEstimate, snapshot: FeatureSnapshotPayload):
        self.calls += 1
        return NarrativeCandidate(
            assumptions_llm=["Order-book coverage supports the base case."],
            risks_flagged=["Execution timeline risk on a recent large order."],
        )


class TestGenerateEstimateEndToEnd:
    async def test_full_stack_produces_estimate_from_historical_quarters(
        self, db_session, company
    ) -> None:
        await _make_quarter_result(
            db_session, company, 2024, "Q4", date(2024, 3, 31), "Q4FY24", revenue=950
        )
        await _make_quarter_result(
            db_session, company, 2025, "Q1", date(2024, 6, 30), "Q1FY25", revenue=1000
        )
        await _make_quarter_result(
            db_session, company, 2025, "Q2", date(2024, 9, 30), "Q2FY25", revenue=1050
        )
        target_period = await _make_quarter_result(
            db_session, company, 2025, "Q3", date(2024, 12, 31), "Q3FY25", revenue=1100
        )
        cutoff = datetime.now(UTC)

        service = EstimationService(db_session, narrator=None)
        run = await service.generate_estimate(
            company_id=company.id, financial_period_id=target_period.id, cutoff_at=cutoff
        )
        await db_session.flush()

        assert run.revenue_base is not None
        assert run.model_version == "deterministic-v1"
        assert len(run.assumptions) > 0

    async def test_snapshot_is_reused_but_a_new_run_is_created_each_call(
        self, db_session, company
    ) -> None:
        await _make_quarter_result(
            db_session, company, 2025, "Q1", date(2024, 6, 30), "Q1FY25", revenue=1000
        )
        target_period = await _make_quarter_result(
            db_session, company, 2025, "Q2", date(2024, 9, 30), "Q2FY25", revenue=1050
        )
        cutoff = datetime.now(UTC)

        service = EstimationService(db_session, narrator=None)
        first = await service.generate_estimate(
            company_id=company.id, financial_period_id=target_period.id, cutoff_at=cutoff
        )
        await db_session.flush()
        second = await service.generate_estimate(
            company_id=company.id, financial_period_id=target_period.id, cutoff_at=cutoff
        )
        await db_session.flush()

        assert first.id != second.id
        assert first.feature_snapshot_id == second.feature_snapshot_id

    async def test_narrator_assumptions_persist_without_altering_numeric_fields(
        self, db_session, company
    ) -> None:
        await _make_quarter_result(
            db_session, company, 2025, "Q1", date(2024, 6, 30), "Q1FY25", revenue=1000
        )
        await _make_quarter_result(
            db_session, company, 2025, "Q2", date(2024, 9, 30), "Q2FY25", revenue=1050
        )
        target_period = await _make_quarter_result(
            db_session, company, 2025, "Q3", date(2024, 12, 31), "Q3FY25", revenue=1100
        )
        cutoff = datetime.now(UTC)

        fake_narrator = FakeNarrator()
        service_no_llm = EstimationService(db_session, narrator=None)
        baseline = await service_no_llm.generate_estimate(
            company_id=company.id, financial_period_id=target_period.id, cutoff_at=cutoff
        )
        await db_session.flush()

        service_with_llm = EstimationService(db_session, narrator=fake_narrator)
        with_narrative = await service_with_llm.generate_estimate(
            company_id=company.id, financial_period_id=target_period.id, cutoff_at=cutoff
        )
        await db_session.flush()

        assert fake_narrator.calls == 1
        assert with_narrative.model_version == "deterministic-v1+narrator-v1"
        assert with_narrative.revenue_base == baseline.revenue_base
        assert with_narrative.pat_base == baseline.pat_base
        llm_assumptions = [a for a in with_narrative.assumptions if a["source"] == "llm"]
        assert len(llm_assumptions) == 2


class TestPointInTimeLeakageGuard:
    async def test_target_periods_own_actual_result_never_leaks_into_its_own_history(
        self, db_session, company
    ) -> None:
        """Simulates the exact backtesting scenario: a quarter's own actual
        result has already been ingested (e.g. results day has passed and a
        later sync pulled it in), but we're generating a *historical*
        estimate for that same quarter using a cutoff captured right after
        only the comparable prior quarters were inserted. The target
        period's own result must not appear in historical_financials even
        though it satisfies available_at <= cutoff_at for a cutoff taken
        after it too — the leakage guard is period-based, not just
        available_at-based."""
        await _make_quarter_result(
            db_session, company, 2025, "Q1", date(2024, 6, 30), "Q1FY25", revenue=1000
        )
        await _make_quarter_result(
            db_session, company, 2025, "Q2", date(2024, 9, 30), "Q2FY25", revenue=1050
        )
        target_period = await _make_quarter_result(
            db_session, company, 2025, "Q3", date(2024, 12, 31), "Q3FY25", revenue=1100
        )
        # cutoff captured *after* the target period's own actual result was
        # inserted — proves the guard isn't relying on available_at alone.
        cutoff = datetime.now(UTC)

        from investing_agent.services.estimation.features import build_feature_snapshot

        snapshot_row = await build_feature_snapshot(
            db_session, company.id, target_period.id, cutoff
        )
        await db_session.flush()

        payload = FeatureSnapshotPayload(**snapshot_row.payload)
        seen_period_ends = {p.period_end for p in payload.historical_financials}
        seen_revenues = {p.revenue for p in payload.historical_financials}

        assert date(2024, 12, 31) not in seen_period_ends
        from decimal import Decimal

        assert Decimal("1100") not in seen_revenues
        assert len(payload.historical_financials) == 2

    async def test_generate_estimate_for_historical_quarter_excludes_its_own_actuals(
        self, db_session, company
    ) -> None:
        await _make_quarter_result(
            db_session, company, 2024, "Q4", date(2024, 3, 31), "Q4FY24", revenue=950
        )
        await _make_quarter_result(
            db_session, company, 2025, "Q1", date(2024, 6, 30), "Q1FY25", revenue=1000
        )
        await _make_quarter_result(
            db_session, company, 2025, "Q2", date(2024, 9, 30), "Q2FY25", revenue=1050
        )
        # The target period's own actual result is already in the DB (the
        # exact backtest scenario) -- revenue=1100 must never influence its
        # own estimate even though it satisfies available_at <= cutoff_at.
        target_period = await _make_quarter_result(
            db_session, company, 2025, "Q3", date(2024, 12, 31), "Q3FY25", revenue=1100
        )
        cutoff = datetime.now(UTC)

        service = EstimationService(db_session, narrator=None)
        run = await service.generate_estimate(
            company_id=company.id, financial_period_id=target_period.id, cutoff_at=cutoff
        )
        await db_session.flush()

        # QoQ samples: (1000-950)/950 and (1050-1000)/1000, median growth
        # applied to the most recent point (1050) -- this is only correct
        # if the target period's own 1100 actual was excluded as a
        # comparable (it would otherwise shift both the sample set and the
        # anchor).
        from decimal import Decimal
        import statistics

        samples = [(Decimal("1000") - Decimal("950")) / Decimal("950"), (Decimal("1050") - Decimal("1000")) / Decimal("1000")]
        median_growth = Decimal(str(statistics.median(samples)))
        expected_base = (Decimal("1050") * (1 + median_growth)).quantize(Decimal("0.01"))

        assert run.revenue_base is not None
        assert run.revenue_base == expected_base
