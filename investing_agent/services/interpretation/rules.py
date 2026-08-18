from __future__ import annotations

"""Deterministic (non-LLM) event interpretation rules — Phase 4B's first
pass over a NewsEvent's representative_headline, run before falling back to
ClaudeEventInterpreter for ambiguous cases.

Each rule is a pure function: headline text in, InterpretationCandidate or
None out. No DB, no network, no LLM — a phrase-list match against the
normalized headline, same conservative whole-phrase-substring approach as
services/matching/company_matcher.py (case-insensitive, no stemming/NLP).
Rules run in a fixed order (_RULES below); the first match wins so a
headline that could plausibly match two categories (rare, given how narrow
each phrase list is) doesn't silently produce two competing candidates for
the same event.

A rule match still yields review_status="pending" downstream (see
services/interpretation/service.py) — even an "obvious" order win needs a
human to confirm the order is material before it becomes a tracked
Catalyst. Rules only decide the *classification*, never bypass review.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

RULES_VERSION = "rules-v1"


@dataclass(frozen=True)
class InterpretationCandidate:
    impact_classification: dict[str, dict[str, str]]
    rationale: str
    candidate_catalyst: dict | None
    candidate_risk: dict | None
    candidate_thesis_change: dict | None
    extraction_method: str
    extractor_version: str
    confidence: Decimal


def _matches_any(text_lower: str, phrases: list[str]) -> bool:
    return any(phrase in text_lower for phrase in phrases)


def _impact(dimension: str, direction: str, magnitude: str) -> dict[str, dict[str, str]]:
    return {dimension: {"direction": direction, "magnitude": magnitude}}


def _rule_order_win(headline: str, text_lower: str) -> InterpretationCandidate | None:
    phrases = [
        "wins order", "bags order", "secures order", "receives order",
        "order worth", "order from", "contract worth", "bags contract",
        "wins contract",
    ]
    if not _matches_any(text_lower, phrases):
        return None
    return InterpretationCandidate(
        impact_classification={
            **_impact("revenue", "positive", "medium"),
            **_impact("order_book", "positive", "medium"),
        },
        rationale=f"Headline reports a new order/contract win: {headline!r}.",
        candidate_catalyst={
            "description": f"Order win reported: {headline}",
            "catalyst_type": "order_win",
            "status": "active",
        },
        candidate_risk=None,
        candidate_thesis_change=None,
        extraction_method="DETERMINISTIC",
        extractor_version=RULES_VERSION,
        confidence=Decimal("0.60"),
    )


def _rule_management_resignation(headline: str, text_lower: str) -> InterpretationCandidate | None:
    phrases = [
        "resigns", "resignation of", "steps down", "quits as", "quits the board",
        "removed as director", "ceases to be",
    ]
    if not _matches_any(text_lower, phrases):
        return None
    return InterpretationCandidate(
        impact_classification=_impact("management", "negative", "medium"),
        rationale=f"Headline reports a management departure: {headline!r}.",
        candidate_catalyst=None,
        candidate_risk={
            "description": f"Management change reported: {headline}",
            "risk_type": "management_change",
            "severity": "medium",
        },
        candidate_thesis_change=None,
        extraction_method="DETERMINISTIC",
        extractor_version=RULES_VERSION,
        confidence=Decimal("0.55"),
    )


def _rule_dividend(headline: str, text_lower: str) -> InterpretationCandidate | None:
    phrases = ["declares dividend", "interim dividend", "final dividend", "special dividend"]
    if not _matches_any(text_lower, phrases):
        return None
    return InterpretationCandidate(
        impact_classification=_impact("capital_allocation", "positive", "low"),
        rationale=f"Headline reports a declared dividend: {headline!r}.",
        candidate_catalyst=None,
        candidate_risk=None,
        candidate_thesis_change={
            "change_type": "other",
            "reason": f"Dividend declared: {headline}",
        },
        extraction_method="DETERMINISTIC",
        extractor_version=RULES_VERSION,
        confidence=Decimal("0.70"),
    )


def _rule_buyback(headline: str, text_lower: str) -> InterpretationCandidate | None:
    phrases = ["share buyback", "buyback of shares", "approves buyback", "buyback offer"]
    if not _matches_any(text_lower, phrases):
        return None
    return InterpretationCandidate(
        impact_classification=_impact("capital_allocation", "positive", "medium"),
        rationale=f"Headline reports a share buyback: {headline!r}.",
        candidate_catalyst={
            "description": f"Buyback announced: {headline}",
            "catalyst_type": "buyback",
            "status": "active",
        },
        candidate_risk=None,
        candidate_thesis_change=None,
        extraction_method="DETERMINISTIC",
        extractor_version=RULES_VERSION,
        confidence=Decimal("0.60"),
    )


def _rule_regulatory_approval(headline: str, text_lower: str) -> InterpretationCandidate | None:
    phrases = [
        "receives approval", "gets approval", "regulatory approval", "gets nod",
        "receives clearance", "environmental clearance", "gets certification",
    ]
    if not _matches_any(text_lower, phrases):
        return None
    return InterpretationCandidate(
        impact_classification={
            **_impact("regulation", "positive", "medium"),
            **_impact("order_book", "positive", "low"),
        },
        rationale=f"Headline reports a regulatory approval/clearance: {headline!r}.",
        candidate_catalyst={
            "description": f"Regulatory approval received: {headline}",
            "catalyst_type": "regulatory_approval",
            "status": "active",
        },
        candidate_risk=None,
        candidate_thesis_change=None,
        extraction_method="DETERMINISTIC",
        extractor_version=RULES_VERSION,
        confidence=Decimal("0.55"),
    )


def _rule_plant_shutdown(headline: str, text_lower: str) -> InterpretationCandidate | None:
    phrases = [
        "shuts plant", "plant shutdown", "halts production", "suspends operations",
        "shuts down plant", "temporarily shuts",
    ]
    if not _matches_any(text_lower, phrases):
        return None
    return InterpretationCandidate(
        impact_classification={
            **_impact("revenue", "negative", "medium"),
            **_impact("order_book", "negative", "medium"),
        },
        rationale=f"Headline reports a plant shutdown/production halt: {headline!r}.",
        candidate_catalyst=None,
        candidate_risk={
            "description": f"Plant shutdown reported: {headline}",
            "risk_type": "operational",
            "severity": "medium",
        },
        candidate_thesis_change=None,
        extraction_method="DETERMINISTIC",
        extractor_version=RULES_VERSION,
        confidence=Decimal("0.60"),
    )


def _rule_rating_downgrade(headline: str, text_lower: str) -> InterpretationCandidate | None:
    phrases = [
        "downgrades rating", "rating downgraded", "cuts rating", "downgraded to sell",
        "downgraded to hold", "downgrades to",
    ]
    if not _matches_any(text_lower, phrases):
        return None
    return InterpretationCandidate(
        impact_classification=_impact("valuation", "negative", "medium"),
        rationale=f"Headline reports a credit/brokerage rating downgrade: {headline!r}.",
        candidate_catalyst=None,
        candidate_risk={
            "description": f"Rating downgrade reported: {headline}",
            "risk_type": "rating_downgrade",
            "severity": "medium",
        },
        candidate_thesis_change=None,
        extraction_method="DETERMINISTIC",
        extractor_version=RULES_VERSION,
        confidence=Decimal("0.55"),
    )


def _rule_capex_announcement(headline: str, text_lower: str) -> InterpretationCandidate | None:
    phrases = [
        "capex of", "capital expenditure", "to invest rs", "announces investment",
        "new plant", "expansion plan", "to set up new",
    ]
    if not _matches_any(text_lower, phrases):
        return None
    return InterpretationCandidate(
        impact_classification={
            **_impact("capital_allocation", "neutral", "medium"),
            **_impact("order_book", "positive", "low"),
        },
        rationale=f"Headline reports a capex/expansion announcement: {headline!r}.",
        candidate_catalyst={
            "description": f"Capex/expansion announced: {headline}",
            "catalyst_type": "capex",
            "status": "active",
        },
        candidate_risk=None,
        candidate_thesis_change=None,
        extraction_method="DETERMINISTIC",
        extractor_version=RULES_VERSION,
        confidence=Decimal("0.50"),
    )


_RULES: list[Callable[[str, str], InterpretationCandidate | None]] = [
    _rule_order_win,
    _rule_management_resignation,
    _rule_dividend,
    _rule_buyback,
    _rule_regulatory_approval,
    _rule_plant_shutdown,
    _rule_rating_downgrade,
    _rule_capex_announcement,
]


def interpret_deterministic(headline: str) -> InterpretationCandidate | None:
    """First matching rule wins, in the fixed order above. None means no
    rule fired — the caller should fall back to the LLM interpreter."""
    text_lower = headline.lower()
    for rule in _RULES:
        candidate = rule(headline, text_lower)
        if candidate is not None:
            return candidate
    return None
