from __future__ import annotations

"""Unit tests for services/matching/company_matcher.py — BEL/HAL alias
matching, HAL bare-symbol ambiguity downgrade, and multi-alias-type
coexistence. Uses the same seed data as
services/matching/seed_aliases.py::DEFAULT_ALIAS_SEEDS, constructed directly
as in-memory CompanyAlias rows (no DB).
"""

import uuid
from decimal import Decimal

from investing_agent.db.models import CompanyAlias
from investing_agent.services.matching.company_matcher import CompanyMatcher

BEL_ID = uuid.uuid4()
HAL_ID = uuid.uuid4()


def _alias(company_id: uuid.UUID, alias: str, alias_type: str, confidence: str) -> CompanyAlias:
    return CompanyAlias(
        company_id=company_id,
        alias=alias,
        alias_type=alias_type,
        match_confidence=Decimal(confidence),
        is_active=True,
    )


def _default_aliases() -> list[CompanyAlias]:
    return [
        _alias(BEL_ID, "Bharat Electronics", "full_name", "1.00"),
        _alias(BEL_ID, "Bharat Electronics Ltd", "full_name", "1.00"),
        _alias(BEL_ID, "BEL", "symbol", "0.70"),
        _alias(HAL_ID, "Hindustan Aeronautics", "full_name", "1.00"),
        _alias(HAL_ID, "Hindustan Aeronautics Ltd", "full_name", "1.00"),
        _alias(HAL_ID, "HAL", "symbol", "0.20"),
    ]


class TestCompanyMatcherFullName:
    def test_bel_full_name_match(self) -> None:
        matcher = CompanyMatcher(_default_aliases())
        matches = matcher.match("Bharat Electronics wins order from Indian Navy")
        assert len(matches) == 1
        assert matches[0].company_id == BEL_ID
        assert matches[0].match_method == "alias_full_name"
        assert matches[0].relevance_score == Decimal("1.00")

    def test_hal_full_name_match(self) -> None:
        matcher = CompanyMatcher(_default_aliases())
        matches = matcher.match("Hindustan Aeronautics delivers Tejas jets")
        assert len(matches) == 1
        assert matches[0].company_id == HAL_ID
        assert matches[0].match_method == "alias_full_name"


class TestCompanyMatcherSymbol:
    def test_bel_bare_symbol_above_threshold_is_strong_match(self) -> None:
        matcher = CompanyMatcher(_default_aliases())
        matches = matcher.match("BEL stock jumps 5% on order win")
        assert len(matches) == 1
        assert matches[0].company_id == BEL_ID
        assert matches[0].match_method == "alias_symbol"
        assert matches[0].relevance_score == Decimal("0.70")

    def test_hal_bare_symbol_below_threshold_is_downgraded_not_dropped(self) -> None:
        matcher = CompanyMatcher(_default_aliases())
        matches = matcher.match("HAL shares rally after defence order")
        assert len(matches) == 1
        assert matches[0].company_id == HAL_ID
        assert matches[0].match_method == "alias_symbol_ambiguous"
        assert matches[0].relevance_score == Decimal("0.20")

    def test_hal_symbol_plus_full_name_wins_as_name_hit(self) -> None:
        matcher = CompanyMatcher(_default_aliases())
        matches = matcher.match("HAL (Hindustan Aeronautics) delivers Tejas jets")
        assert len(matches) == 1
        assert matches[0].match_method == "alias_full_name"
        assert matches[0].relevance_score == Decimal("1.00")


class TestCompanyMatcherNoMatch:
    def test_no_match_returns_empty_list(self) -> None:
        matcher = CompanyMatcher(_default_aliases())
        matches = matcher.match("Reliance Industries announces quarterly results")
        assert matches == []

    def test_substring_does_not_match_whole_word_boundary(self) -> None:
        # "HALT" contains "HAL" as a substring but must not whole-word match.
        matcher = CompanyMatcher(_default_aliases())
        matches = matcher.match("Trading halted on BSE due to circuit breaker")
        assert matches == []

    def test_inactive_alias_never_matches(self) -> None:
        aliases = _default_aliases()
        aliases.append(
            CompanyAlias(
                company_id=uuid.uuid4(),
                alias="Inactive Corp",
                alias_type="full_name",
                match_confidence=Decimal("1.00"),
                is_active=False,
            )
        )
        matcher = CompanyMatcher(aliases)
        matches = matcher.match("Inactive Corp announces results")
        assert matches == []


class TestCompanyMatcherMultipleCompanies:
    def test_both_companies_matched_independently(self) -> None:
        matcher = CompanyMatcher(_default_aliases())
        matches = matcher.match(
            "Bharat Electronics and Hindustan Aeronautics both win defence orders"
        )
        company_ids = {m.company_id for m in matches}
        assert company_ids == {BEL_ID, HAL_ID}
        assert all(m.match_method == "alias_full_name" for m in matches)
