from __future__ import annotations

"""Unit tests for services/interpretation/llm_interpreter.py.

No live Anthropic call anywhere in this file: _to_candidate is tested as a
pure mapping function, ClaudeEventInterpreter.__init__'s API-key guard is
tested via a monkeypatched get_settings, and interpret()'s error-swallowing
behavior is tested against a stub object standing in for the langchain
runnable (never a real ChatAnthropic).
"""

from types import SimpleNamespace

import pytest

from investing_agent.services.interpretation import llm_interpreter as llm_interpreter_module
from investing_agent.services.interpretation.llm_interpreter import (
    ClaudeEventInterpreter,
    _DimensionAssessment,
    _LLMInterpretationOutput,
    _to_candidate,
)


def _output(**overrides) -> _LLMInterpretationOutput:
    defaults = dict(
        impact_classification={
            "revenue": _DimensionAssessment(direction="positive", magnitude="medium")
        },
        rationale="BEL announced a new defence order.",
        suggests_catalyst=False,
        catalyst_description=None,
        catalyst_type=None,
        suggests_risk=False,
        risk_description=None,
        risk_type=None,
        risk_severity=None,
        confidence=0.75,
    )
    defaults.update(overrides)
    return _LLMInterpretationOutput(**defaults)


class TestToCandidate:
    def test_maps_impact_classification_and_rationale(self) -> None:
        candidate = _to_candidate(_output(), model_name="claude-sonnet-4-6")
        assert candidate.impact_classification == {
            "revenue": {"direction": "positive", "magnitude": "medium"}
        }
        assert candidate.rationale == "BEL announced a new defence order."
        assert candidate.extraction_method == "LLM_ASSISTED"
        assert candidate.extractor_version == "llm-interpreter-v1:claude-sonnet-4-6"

    def test_confidence_rounded_to_two_decimals(self) -> None:
        candidate = _to_candidate(_output(confidence=0.7345), model_name="m")
        assert str(candidate.confidence) == "0.73"

    def test_suggests_catalyst_builds_candidate_catalyst(self) -> None:
        candidate = _to_candidate(
            _output(
                suggests_catalyst=True,
                catalyst_description="New order pipeline visibility improves.",
                catalyst_type="order_win",
            ),
            model_name="m",
        )
        assert candidate.candidate_catalyst == {
            "description": "New order pipeline visibility improves.",
            "catalyst_type": "order_win",
            "status": "active",
        }
        assert candidate.candidate_risk is None

    def test_suggests_catalyst_without_description_produces_no_candidate(self) -> None:
        candidate = _to_candidate(
            _output(suggests_catalyst=True, catalyst_description=None), model_name="m"
        )
        assert candidate.candidate_catalyst is None

    def test_suggests_risk_builds_candidate_risk(self) -> None:
        candidate = _to_candidate(
            _output(
                suggests_risk=True,
                risk_description="Execution delay risk noted.",
                risk_type="operational",
                risk_severity="medium",
            ),
            model_name="m",
        )
        assert candidate.candidate_risk == {
            "description": "Execution delay risk noted.",
            "risk_type": "operational",
            "severity": "medium",
        }

    def test_candidate_thesis_change_always_none(self) -> None:
        # The LLM path never proposes a thesis change directly — only
        # catalyst/risk; thesis changes are a human-only escalation for now.
        candidate = _to_candidate(_output(), model_name="m")
        assert candidate.candidate_thesis_change is None


class TestClaudeEventInterpreterApiKeyGuard:
    def test_raises_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            llm_interpreter_module,
            "get_settings",
            lambda: SimpleNamespace(anthropic_api_key=None, anthropic_model="claude-sonnet-4-6"),
        )
        with pytest.raises(ValueError, match="anthropic_api_key"):
            ClaudeEventInterpreter()


class TestClaudeEventInterpreterCallFailureHandling:
    @pytest.mark.asyncio
    async def test_exception_from_llm_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        interpreter = ClaudeEventInterpreter.__new__(ClaudeEventInterpreter)
        interpreter._model_name = "claude-sonnet-4-6"

        class _BoomLLM:
            async def ainvoke(self, *args, **kwargs):
                raise RuntimeError("simulated API failure")

        interpreter._llm = _BoomLLM()

        result = await interpreter.interpret(
            headline="BEL wins order", company_name="Bharat Electronics",
            company_symbol="BEL", event_type="unclassified",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_unexpected_output_type_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        interpreter = ClaudeEventInterpreter.__new__(ClaudeEventInterpreter)
        interpreter._model_name = "claude-sonnet-4-6"

        class _WrongTypeLLM:
            async def ainvoke(self, *args, **kwargs):
                return "not a structured output object"

        interpreter._llm = _WrongTypeLLM()

        result = await interpreter.interpret(
            headline="BEL wins order", company_name="Bharat Electronics",
            company_symbol="BEL", event_type="unclassified",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_valid_output_maps_to_candidate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        interpreter = ClaudeEventInterpreter.__new__(ClaudeEventInterpreter)
        interpreter._model_name = "claude-sonnet-4-6"

        class _GoodLLM:
            async def ainvoke(self, *args, **kwargs):
                return _output(rationale="Positive order news for BEL.")

        interpreter._llm = _GoodLLM()

        result = await interpreter.interpret(
            headline="BEL wins order", company_name="Bharat Electronics",
            company_symbol="BEL", event_type="unclassified",
        )
        assert result is not None
        assert result.rationale == "Positive order news for BEL."
        assert result.extraction_method == "LLM_ASSISTED"
