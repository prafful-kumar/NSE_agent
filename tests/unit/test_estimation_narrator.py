from __future__ import annotations

"""Unit tests for services/estimation/narrator.py.

No live Anthropic call anywhere: the type-level "LLM cannot invent a
number" guarantee is verified via NarrativeCandidate.model_fields
introspection, __init__'s API-key guard is tested via a monkeypatched
get_settings, and narrate()'s error-swallowing behavior is tested against a
stub standing in for the langchain runnable — same pattern as
tests/unit/test_llm_interpreter.py.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from investing_agent.schemas.estimation import FeatureSnapshotPayload, TargetPeriodInfo
from investing_agent.services.estimation import narrator as narrator_module
from investing_agent.services.estimation.deterministic import DeterministicEstimate, MetricEstimate
from investing_agent.services.estimation.narrator import (
    ClaudeEstimateNarrator,
    NarrativeCandidate,
)


def _snapshot() -> FeatureSnapshotPayload:
    return FeatureSnapshotPayload(
        target_period=TargetPeriodInfo(
            fiscal_year=2025, quarter="Q3", period_type="quarter",
            period_end=date(2024, 12, 31), label="Q3FY25",
        )
    )


def _computed() -> DeterministicEstimate:
    return DeterministicEstimate(
        revenue=MetricEstimate(low=Decimal("100"), base=Decimal("110"), high=Decimal("120")),
    )


class TestNarrativeCandidateHasNoNumericField:
    def test_no_field_is_a_decimal_float_or_int(self) -> None:
        for name, field in NarrativeCandidate.model_fields.items():
            assert field.annotation not in (int, float, Decimal), (
                f"NarrativeCandidate.{name} must never be numeric — the LLM "
                "narrator must not be able to influence a stored estimate number."
            )

    def test_only_expected_fields_exist(self) -> None:
        assert set(NarrativeCandidate.model_fields) == {"assumptions_llm", "risks_flagged"}


class TestClaudeEstimateNarratorApiKeyGuard:
    def test_raises_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            narrator_module,
            "get_settings",
            lambda: SimpleNamespace(anthropic_api_key=None, anthropic_model="claude-sonnet-4-6"),
        )
        with pytest.raises(ValueError, match="anthropic_api_key"):
            ClaudeEstimateNarrator()


class TestClaudeEstimateNarratorCallFailureHandling:
    async def test_exception_from_llm_returns_none(self) -> None:
        instance = ClaudeEstimateNarrator.__new__(ClaudeEstimateNarrator)

        class _BoomLLM:
            async def ainvoke(self, *args, **kwargs):
                raise RuntimeError("simulated API failure")

        instance._llm = _BoomLLM()
        result = await instance.narrate(computed=_computed(), snapshot=_snapshot())
        assert result is None

    async def test_unexpected_output_type_returns_none(self) -> None:
        instance = ClaudeEstimateNarrator.__new__(ClaudeEstimateNarrator)

        class _WrongTypeLLM:
            async def ainvoke(self, *args, **kwargs):
                return "not a structured output object"

        instance._llm = _WrongTypeLLM()
        result = await instance.narrate(computed=_computed(), snapshot=_snapshot())
        assert result is None

    async def test_valid_output_is_returned_unchanged(self) -> None:
        instance = ClaudeEstimateNarrator.__new__(ClaudeEstimateNarrator)
        candidate = NarrativeCandidate(
            assumptions_llm=["Growth is supported by strong order-book coverage."],
            risks_flagged=["Execution delay risk on a large recent order."],
        )

        class _GoodLLM:
            async def ainvoke(self, *args, **kwargs):
                return candidate

        instance._llm = _GoodLLM()
        result = await instance.narrate(computed=_computed(), snapshot=_snapshot())
        assert result is candidate
