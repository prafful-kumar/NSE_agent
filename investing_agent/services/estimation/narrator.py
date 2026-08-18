from __future__ import annotations

"""EstimateNarrator: optional LLM-assisted qualitative assumptions for an
already-computed DeterministicEstimate.

Every numeric field on an EstimateRun (revenue/margin/pat/eps low/base/high,
confidence) is fixed by services/estimation/deterministic.py BEFORE this
module is ever invoked. NarrativeCandidate — the only type this module can
return — has no numeric field on it at all: assumptions_llm and
risks_flagged are both list[str]. This makes "the LLM never invents a
number" a type-level guarantee, not just a convention — stronger than
Phase 4B's review-gated InterpretationCandidate, which does carry numeric
confidence/impact fields pending human review.

Same operational pattern as services/interpretation/llm_interpreter.py:
kept behind an ABC so tests never touch a real LLM, degrades to None (never
raises) on any failure, so a narration failure never blocks persisting the
already-computed deterministic estimate.
"""

from abc import ABC, abstractmethod

import structlog
from pydantic import BaseModel, Field

from investing_agent.config.settings import get_settings
from investing_agent.schemas.estimation import FeatureSnapshotPayload
from investing_agent.services.estimation.deterministic import DeterministicEstimate

log = structlog.get_logger(__name__)

NARRATOR_VERSION_PREFIX = "estimate-narrator-v1"


class NarrativeCandidate(BaseModel):
    """No numeric field exists on this type — see module docstring. Do not
    add one; a Decimal/float/int field here would silently break the
    guarantee that the LLM cannot influence a stored estimate number."""

    assumptions_llm: list[str] = Field(
        description="Qualitative assumptions explaining the already-computed base case, "
        "grounded strictly in the provided facts (guidance, order book, segment data, "
        "recent news). Do not restate or introduce any numbers of your own."
    )
    risks_flagged: list[str] = Field(
        default_factory=list,
        description="Qualitative risks a human should weigh that the numeric estimate can't "
        "capture (e.g. an unresolved regulatory risk, a pending management change).",
    )


_SYSTEM_PROMPT = """You are a conservative equity-research assistant. You have been given an \
already-computed earnings estimate (revenue/margin/PAT/EPS ranges) for one company — those \
numbers are fixed and you must NOT restate, recompute, or contradict them with numbers of your \
own. Your only job is to explain, in plain language, why the computed base case is reasonable \
given the facts provided (or where it might be shaky), and to flag qualitative risks the \
numbers don't capture. Do not invent facts not present in the provided data."""


class EstimateNarrator(ABC):
    @abstractmethod
    async def narrate(
        self, *, computed: DeterministicEstimate, snapshot: FeatureSnapshotPayload
    ) -> NarrativeCandidate | None:
        """Returns a NarrativeCandidate, or None if the LLM call failed."""


class ClaudeEstimateNarrator(EstimateNarrator):
    def __init__(self, model: str | None = None) -> None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise ValueError(
                "ClaudeEstimateNarrator requires settings.anthropic_api_key to be set"
            )
        self._model_name = model or settings.anthropic_model
        from langchain_anthropic import ChatAnthropic

        self._llm = ChatAnthropic(
            model=self._model_name,
            api_key=settings.anthropic_api_key,
            temperature=0,
        ).with_structured_output(NarrativeCandidate)

    async def narrate(
        self, *, computed: DeterministicEstimate, snapshot: FeatureSnapshotPayload
    ) -> NarrativeCandidate | None:
        prompt = _build_prompt(computed, snapshot)
        try:
            output = await self._llm.ainvoke(
                [("system", _SYSTEM_PROMPT), ("human", prompt)]
            )
        except Exception:
            log.warning("estimate_narrator.call_failed", exc_info=True)
            return None

        if not isinstance(output, NarrativeCandidate):
            log.warning("estimate_narrator.unexpected_output_type", output_type=type(output))
            return None

        return output


def _build_prompt(computed: DeterministicEstimate, snapshot: FeatureSnapshotPayload) -> str:
    lines = [
        f"Target period: {snapshot.target_period.label}",
        f"Computed revenue: low={computed.revenue.low} base={computed.revenue.base} "
        f"high={computed.revenue.high}",
        f"Computed EBITDA margin %: low={computed.ebitda_margin.low} "
        f"base={computed.ebitda_margin.base} high={computed.ebitda_margin.high}",
        f"Computed PAT: low={computed.pat.low} base={computed.pat.base} high={computed.pat.high}",
        f"Computed EPS: low={computed.eps.low} base={computed.eps.base} high={computed.eps.high}",
        f"Confidence: {computed.confidence}",
        "Deterministic assumptions already on record:",
    ]
    lines += [f"- {a.text}" for a in computed.assumptions]
    if snapshot.guidance:
        lines.append("Management guidance on record:")
        lines += [f"- {g.metric_label}: {g.guidance_value_text}" for g in snapshot.guidance]
    if snapshot.order_book:
        ob = snapshot.order_book
        lines.append(
            f"Order book as of {ob.as_of_date}: {ob.order_book_value} {ob.currency} "
            f"(book-to-bill {ob.book_to_bill_ratio}, execution: {ob.expected_execution_period})"
        )
    if snapshot.active_catalysts:
        lines.append("Active catalysts on record:")
        lines += [f"- {c.description}" for c in snapshot.active_catalysts]
    if snapshot.active_risks:
        lines.append("Active risks on record:")
        lines += [f"- {r.description}" for r in snapshot.active_risks]
    if snapshot.recent_news:
        lines.append("Recent news signals:")
        lines += [f"- {n.headline}" for n in snapshot.recent_news]
    return "\n".join(lines)
