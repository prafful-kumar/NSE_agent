from __future__ import annotations

"""Unit tests for services/estimation/service.py::EstimationService.

build_feature_snapshot, estimate_deterministic and the narrator are all
patched — this proves the orchestration contract (snapshot -> compute ->
optional narrate -> persist, with the narrator only ever able to append
source="llm" assumption text) without touching a real DB or LLM. Real
persistence is covered by tests/integration/test_estimation_service_integration.py.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from investing_agent.services.estimation.deterministic import DeterministicEstimate, MetricEstimate
from investing_agent.services.estimation.narrator import NarrativeCandidate
from investing_agent.services.estimation.service import EstimationService

COMPANY_ID = uuid.uuid4()
PERIOD_ID = uuid.uuid4()
SNAPSHOT_ID = uuid.uuid4()
CUTOFF = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def _snapshot_row(payload: dict | None = None) -> MagicMock:
    row = MagicMock(id=SNAPSHOT_ID)
    row.payload = payload or {
        "target_period": {
            "fiscal_year": 2026, "quarter": "Q3", "period_type": "quarter",
            "period_end": "2025-12-31", "label": "Q3FY26",
        }
    }
    return row


def _computed(**overrides) -> DeterministicEstimate:
    defaults = dict(
        revenue=MetricEstimate(low=Decimal("100"), base=Decimal("110"), high=Decimal("120")),
        ebitda_margin=MetricEstimate(low=Decimal("10"), base=Decimal("11"), high=Decimal("12")),
        pat=MetricEstimate(low=Decimal("8"), base=Decimal("9"), high=Decimal("10")),
        eps=MetricEstimate(low=Decimal("1.1"), base=Decimal("1.2"), high=Decimal("1.3")),
        confidence=Decimal("0.6"),
        assumptions=[],
    )
    defaults.update(overrides)
    return DeterministicEstimate(**defaults)


class TestGenerateEstimateWithoutNarrator:
    async def test_persists_quantized_deterministic_fields(self) -> None:
        session = AsyncMock()
        with (
            patch(
                "investing_agent.services.estimation.service.build_feature_snapshot",
                AsyncMock(return_value=_snapshot_row()),
            ),
            patch(
                "investing_agent.services.estimation.service.estimate_deterministic",
                MagicMock(return_value=_computed()),
            ),
            patch(
                "investing_agent.services.estimation.service.EstimateRunRepository"
            ) as repo_cls,
        ):
            repo_cls.return_value.create = AsyncMock(
                side_effect=lambda data: MagicMock(**data.model_dump())
            )
            service = EstimationService(session, narrator=None)
            run = await service.generate_estimate(
                company_id=COMPANY_ID, financial_period_id=PERIOD_ID, cutoff_at=CUTOFF
            )

        assert run.model_version == "deterministic-v1"
        assert run.feature_snapshot_id == SNAPSHOT_ID
        assert run.revenue_base == Decimal("110.00")
        assert run.eps_base == Decimal("1.2000")
        assert run.confidence == Decimal("0.6")

    async def test_no_narrator_leaves_model_version_unsuffixed(self) -> None:
        session = AsyncMock()
        with (
            patch(
                "investing_agent.services.estimation.service.build_feature_snapshot",
                AsyncMock(return_value=_snapshot_row()),
            ),
            patch(
                "investing_agent.services.estimation.service.estimate_deterministic",
                MagicMock(return_value=_computed()),
            ),
            patch(
                "investing_agent.services.estimation.service.EstimateRunRepository"
            ) as repo_cls,
        ):
            created = {}

            async def _capture_create(data):
                created["data"] = data
                return MagicMock(**data.model_dump())

            repo_cls.return_value.create = AsyncMock(side_effect=_capture_create)
            service = EstimationService(session, narrator=None)
            await service.generate_estimate(
                company_id=COMPANY_ID, financial_period_id=PERIOD_ID, cutoff_at=CUTOFF
            )

        assert created["data"].model_version == "deterministic-v1"
        assert created["data"].assumptions == []


class TestGenerateEstimateWithNarrator:
    async def test_narrator_only_appends_text_assumptions_never_touches_numbers(self) -> None:
        session = AsyncMock()
        fake_narrator = AsyncMock()
        fake_narrator.narrate = AsyncMock(
            return_value=NarrativeCandidate(
                assumptions_llm=["Strong order-book coverage supports the base case."],
                risks_flagged=["Execution delay risk on a recent large order."],
            )
        )

        with (
            patch(
                "investing_agent.services.estimation.service.build_feature_snapshot",
                AsyncMock(return_value=_snapshot_row()),
            ),
            patch(
                "investing_agent.services.estimation.service.estimate_deterministic",
                MagicMock(return_value=_computed()),
            ),
            patch(
                "investing_agent.services.estimation.service.EstimateRunRepository"
            ) as repo_cls,
        ):
            created = {}

            async def _capture_create(data):
                created["data"] = data
                return MagicMock(**data.model_dump())

            repo_cls.return_value.create = AsyncMock(side_effect=_capture_create)
            service = EstimationService(session, narrator=fake_narrator)
            run = await service.generate_estimate(
                company_id=COMPANY_ID, financial_period_id=PERIOD_ID, cutoff_at=CUTOFF
            )

        assert run.model_version == "deterministic-v1+narrator-v1"
        assumptions = created["data"].assumptions
        assert len(assumptions) == 2
        assert all(a.source == "llm" for a in assumptions)
        assert "Strong order-book coverage" in assumptions[0].text
        assert "Risk flagged: Execution delay risk" in assumptions[1].text
        # Numeric fields are exactly what the deterministic computation
        # produced — the narrator had no path to change them.
        assert run.revenue_base == Decimal("110.00")
        assert run.pat_base == Decimal("9.00")

    async def test_narrator_returning_none_leaves_model_version_unsuffixed(self) -> None:
        session = AsyncMock()
        fake_narrator = AsyncMock()
        fake_narrator.narrate = AsyncMock(return_value=None)

        with (
            patch(
                "investing_agent.services.estimation.service.build_feature_snapshot",
                AsyncMock(return_value=_snapshot_row()),
            ),
            patch(
                "investing_agent.services.estimation.service.estimate_deterministic",
                MagicMock(return_value=_computed()),
            ),
            patch(
                "investing_agent.services.estimation.service.EstimateRunRepository"
            ) as repo_cls,
        ):
            repo_cls.return_value.create = AsyncMock(
                side_effect=lambda data: MagicMock(**data.model_dump())
            )
            service = EstimationService(session, narrator=fake_narrator)
            run = await service.generate_estimate(
                company_id=COMPANY_ID, financial_period_id=PERIOD_ID, cutoff_at=CUTOFF
            )

        assert run.model_version == "deterministic-v1"


class TestGenerateEstimateNoneMetricsQuantizeToNone:
    async def test_none_metric_fields_remain_none_after_quantization(self) -> None:
        session = AsyncMock()
        with (
            patch(
                "investing_agent.services.estimation.service.build_feature_snapshot",
                AsyncMock(return_value=_snapshot_row()),
            ),
            patch(
                "investing_agent.services.estimation.service.estimate_deterministic",
                MagicMock(return_value=DeterministicEstimate()),
            ),
            patch(
                "investing_agent.services.estimation.service.EstimateRunRepository"
            ) as repo_cls,
        ):
            repo_cls.return_value.create = AsyncMock(
                side_effect=lambda data: MagicMock(**data.model_dump())
            )
            service = EstimationService(session, narrator=None)
            run = await service.generate_estimate(
                company_id=COMPANY_ID, financial_period_id=PERIOD_ID, cutoff_at=CUTOFF
            )

        assert run.revenue_base is None
        assert run.eps_high is None
