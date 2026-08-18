from __future__ import annotations

"""EventInterpreter: the LLM fallback for NewsEvents that no deterministic
rule (services/interpretation/rules.py) matched.

Same safety boundary as the rule layer: interpret() only ever returns an
InterpretationCandidate for review — nothing here writes to the DB or to
catalysts/risk_observations/thesis_changes directly. Kept behind an ABC
(mirrors services/embeddings/interfaces.py::EmbeddingProvider) so
services/interpretation/service.py and every test depend on the interface,
never on ChatAnthropic directly — tests use FakeEventInterpreter, never a
live API call.

A single malformed/failed LLM call must not abort a whole interpret-events
run: interpret() catches any exception from the underlying client and
returns None (logged), which the caller treats identically to "no
interpretation produced this pass, try again later" — the event simply
stays uninterpreted rather than the batch crashing.
"""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Literal

import structlog
from pydantic import BaseModel, Field

from investing_agent.config.settings import get_settings
from investing_agent.services.interpretation.rules import InterpretationCandidate

log = structlog.get_logger(__name__)

LLM_EXTRACTOR_VERSION_PREFIX = "llm-interpreter-v1"

_DIMENSIONS = (
    "revenue", "margins", "order_book", "management", "regulation",
    "capital_allocation", "valuation",
)


class _DimensionAssessment(BaseModel):
    direction: Literal["positive", "negative", "neutral"]
    magnitude: Literal["low", "medium", "high"]


class _LLMInterpretationOutput(BaseModel):
    """Structured-output schema the LLM is constrained to produce. Kept
    separate from schemas/interpretation.py's DB-facing EventInterpretationCreate
    — this is a narrower, LLM-friendly shape (flat optional catalyst/risk
    fields instead of nested dicts) that interpret() maps into an
    InterpretationCandidate afterward."""

    impact_classification: dict[str, _DimensionAssessment] = Field(
        description=(
            "Only include dimensions the headline actually bears on, from: "
            + ", ".join(_DIMENSIONS)
        )
    )
    rationale: str = Field(description="One or two sentences citing the headline as evidence.")
    suggests_catalyst: bool = Field(
        description="True if this event is a forward-looking positive trigger worth tracking."
    )
    catalyst_description: str | None = None
    catalyst_type: str | None = Field(
        default=None,
        description="order_win|buyback|regulatory_approval|capex|other, if suggests_catalyst.",
    )
    suggests_risk: bool = Field(
        description="True if this event is a negative/risk factor worth tracking."
    )
    risk_description: str | None = None
    risk_type: str | None = Field(
        default=None,
        description="management_change|operational|rating_downgrade|regulatory|other, if suggests_risk.",
    )
    risk_severity: Literal["low", "medium", "high"] | None = None
    confidence: float = Field(ge=0.0, le=1.0)


_SYSTEM_PROMPT = """You are a conservative equity-research assistant classifying the likely \
business impact of a single Indian-market news headline about one company. You are NEVER \
certain — you are proposing a candidate interpretation a human analyst will review before it \
becomes a tracked catalyst or risk. Only classify dimensions the headline gives clear evidence \
for; omit dimensions with no basis. Do not speculate about numbers not present in the headline. \
Keep the rationale grounded strictly in the headline text provided."""


class EventInterpreter(ABC):
    @abstractmethod
    async def interpret(
        self, *, headline: str, company_name: str, company_symbol: str, event_type: str
    ) -> InterpretationCandidate | None:
        """Returns an InterpretationCandidate, or None if the LLM call
        failed or declined to classify (e.g. headline has no clear
        company-specific business impact)."""


class ClaudeEventInterpreter(EventInterpreter):
    def __init__(self, model: str | None = None) -> None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise ValueError(
                "ClaudeEventInterpreter requires settings.anthropic_api_key to be set"
            )
        self._model_name = model or settings.anthropic_model
        from langchain_anthropic import ChatAnthropic

        self._llm = ChatAnthropic(
            model=self._model_name,
            api_key=settings.anthropic_api_key,
            temperature=0,
        ).with_structured_output(_LLMInterpretationOutput)

    async def interpret(
        self, *, headline: str, company_name: str, company_symbol: str, event_type: str
    ) -> InterpretationCandidate | None:
        prompt = (
            f"Company: {company_name} ({company_symbol})\n"
            f"Event type (heuristic, may be 'unclassified'): {event_type}\n"
            f"Headline: {headline}"
        )
        try:
            output = await self._llm.ainvoke(
                [("system", _SYSTEM_PROMPT), ("human", prompt)]
            )
        except Exception:
            log.warning("llm_interpreter.call_failed", headline=headline, exc_info=True)
            return None

        if not isinstance(output, _LLMInterpretationOutput):
            log.warning("llm_interpreter.unexpected_output_type", output_type=type(output))
            return None

        return _to_candidate(output, model_name=self._model_name)


def _to_candidate(
    output: _LLMInterpretationOutput, *, model_name: str
) -> InterpretationCandidate:
    candidate_catalyst = None
    if output.suggests_catalyst and output.catalyst_description:
        candidate_catalyst = {
            "description": output.catalyst_description,
            "catalyst_type": output.catalyst_type or "other",
            "status": "active",
        }

    candidate_risk = None
    if output.suggests_risk and output.risk_description:
        candidate_risk = {
            "description": output.risk_description,
            "risk_type": output.risk_type or "other",
            "severity": output.risk_severity or "unclassified",
        }

    return InterpretationCandidate(
        impact_classification={
            dim: {"direction": a.direction, "magnitude": a.magnitude}
            for dim, a in output.impact_classification.items()
        },
        rationale=output.rationale,
        candidate_catalyst=candidate_catalyst,
        candidate_risk=candidate_risk,
        candidate_thesis_change=None,
        extraction_method="LLM_ASSISTED",
        extractor_version=f"{LLM_EXTRACTOR_VERSION_PREFIX}:{model_name}",
        confidence=Decimal(str(round(output.confidence, 2))),
    )
