from __future__ import annotations

"""Deterministic company matching for Phase 4A news ingestion.

Mirrors the whole-word matching approach validated in the bake-off
(research/provider_evaluation/news_bakeoff.py::_mentions) but adds a
confidence dimension driven entirely by CompanyAlias.match_confidence — no
symbol is special-cased in this module. A symbol alias below
AMBIGUOUS_THRESHOLD (e.g. HAL, seeded low because "HAL" collides with the
common English word and other tickers — see bake-off REPORT.md §12) is only
ever a strong match when a full/short name alias for the SAME company also
matches the same text; standalone it is downgraded to
match_method="alias_symbol_ambiguous" rather than dropped, so callers can
still see (and filter on) the low-confidence signal.
"""

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from investing_agent.db.models import CompanyAlias

AMBIGUOUS_THRESHOLD = Decimal("0.50")


@dataclass(frozen=True)
class CompanyMatch:
    company_id: uuid.UUID
    relevance_score: Decimal
    match_method: str


def _mentions_alias(text_lower: str, alias: str) -> bool:
    """Whole-word/phrase match, case-insensitive — same conservative bar as
    the bake-off harness so a 3-letter symbol never substring-matches inside
    an unrelated word."""
    return re.search(rf"\b{re.escape(alias.lower())}\b", text_lower) is not None


class CompanyMatcher:
    """Matches free text (headline + feed description) against a set of
    active CompanyAlias rows, returning at most one CompanyMatch per
    company — the strongest alias hit for that company wins."""

    def __init__(self, aliases: list[CompanyAlias]) -> None:
        self._by_company: dict[uuid.UUID, list[CompanyAlias]] = defaultdict(list)
        for alias in aliases:
            if alias.is_active:
                self._by_company[alias.company_id].append(alias)

    def match(self, text: str) -> list[CompanyMatch]:
        text_lower = text.lower()
        results: list[CompanyMatch] = []
        for company_id, aliases in self._by_company.items():
            hit = self._match_company(text_lower, aliases)
            if hit is not None:
                confidence, method = hit
                results.append(CompanyMatch(company_id, confidence, method))
        return results

    def _match_company(
        self, text_lower: str, aliases: list[CompanyAlias]
    ) -> tuple[Decimal, str] | None:
        name_hit: tuple[Decimal, str] | None = None
        symbol_hit: tuple[Decimal, CompanyAlias] | None = None

        for alias in aliases:
            if not _mentions_alias(text_lower, alias.alias):
                continue
            if alias.alias_type in ("full_name", "short_name"):
                method = f"alias_{alias.alias_type}"
                if name_hit is None or alias.match_confidence > name_hit[0]:
                    name_hit = (alias.match_confidence, method)
            elif alias.alias_type == "symbol":
                if symbol_hit is None or alias.match_confidence > symbol_hit[0]:
                    symbol_hit = (alias.match_confidence, alias)

        # A full/short name hit is unambiguous evidence on its own — it wins
        # regardless of whether a symbol also matched.
        if name_hit is not None:
            return name_hit

        if symbol_hit is not None:
            confidence, _alias = symbol_hit
            if confidence >= AMBIGUOUS_THRESHOLD:
                return (confidence, "alias_symbol")
            return (confidence, "alias_symbol_ambiguous")

        return None
