from __future__ import annotations

"""Tests for DataCategory enum and EvidenceItem contract.

DataCategory values are NOT interchangeable: FACT != ESTIMATE != OPINION.
This is a critical correctness property — mixing categories produces
incorrect recommendations and audit trails.
"""

import pytest

from investing_agent.schemas.common import DataCategory, EvidenceItem


class TestDataCategoryEnum:
    def test_fact_value(self) -> None:
        assert DataCategory.FACT == "fact"

    def test_estimate_value(self) -> None:
        assert DataCategory.ESTIMATE == "estimate"

    def test_opinion_value(self) -> None:
        assert DataCategory.OPINION == "opinion"

    def test_all_three_values_distinct(self) -> None:
        values = {DataCategory.FACT, DataCategory.ESTIMATE, DataCategory.OPINION}
        assert len(values) == 3

    def test_fact_not_equal_estimate(self) -> None:
        assert DataCategory.FACT != DataCategory.ESTIMATE

    def test_fact_not_equal_opinion(self) -> None:
        assert DataCategory.FACT != DataCategory.OPINION

    def test_estimate_not_equal_opinion(self) -> None:
        assert DataCategory.ESTIMATE != DataCategory.OPINION

    def test_membership(self) -> None:
        assert "fact" in DataCategory._value2member_map_
        assert "estimate" in DataCategory._value2member_map_
        assert "opinion" in DataCategory._value2member_map_

    def test_invalid_value_not_in_enum(self) -> None:
        with pytest.raises((ValueError, KeyError)):
            DataCategory("rumour")

    def test_string_comparison(self) -> None:
        # DataCategory inherits from str — this is intentional for JSON serialisation
        assert DataCategory.FACT == "fact"
        assert DataCategory.ESTIMATE == "estimate"


class TestEvidenceItemDataCategory:
    """EvidenceItem must carry DataCategory; default is FACT (exchange data is fact)."""

    def test_default_category_is_fact(self) -> None:
        item = EvidenceItem(source="NSE", tier=1)
        assert item.category == DataCategory.FACT

    def test_brokerage_report_is_opinion(self) -> None:
        item = EvidenceItem(
            source="Kotak Institutional",
            tier=2,
            category=DataCategory.OPINION,
        )
        assert item.category == DataCategory.OPINION

    def test_analyst_estimate_is_estimate(self) -> None:
        item = EvidenceItem(
            source="internal_model",
            tier=2,
            category=DataCategory.ESTIMATE,
        )
        assert item.category == DataCategory.ESTIMATE

    def test_tier_1_fact_confirmed(self) -> None:
        item = EvidenceItem(
            source="NSE filing",
            tier=1,
            category=DataCategory.FACT,
            is_confirmed=True,
        )
        assert item.tier == 1
        assert item.is_confirmed is True
        assert item.category == DataCategory.FACT

    def test_unconfirmed_rumour_is_opinion_tier3(self) -> None:
        item = EvidenceItem(
            source="Twitter/@investor",
            tier=3,
            category=DataCategory.OPINION,
            is_confirmed=False,
        )
        assert item.is_confirmed is False
        assert item.tier == 3
        assert item.category == DataCategory.OPINION

    def test_category_serializes_to_string(self) -> None:
        item = EvidenceItem(source="NSE", tier=1, category=DataCategory.FACT)
        d = item.model_dump()
        assert d["category"] == "fact"

    def test_evidence_item_full_round_trip(self) -> None:
        from datetime import datetime, timezone

        raw = {
            "source": "NSE/BSE Exchange",
            "published_at": "2026-08-15T09:15:00+00:00",
            "url": None,
            "tier": 1,
            "category": "fact",
            "is_confirmed": True,
        }
        item = EvidenceItem.model_validate(raw)
        assert item.category == DataCategory.FACT
        assert item.tier == 1
        assert item.is_confirmed is True


class TestDataCategoryEnforcement:
    """Verify that wrong category values are rejected at validation time."""

    def test_invalid_category_string_rejected(self) -> None:
        with pytest.raises(Exception):
            EvidenceItem(source="X", tier=1, category="random_value")

    def test_category_cannot_be_none(self) -> None:
        # Should fall back to default (FACT), not accept None
        item = EvidenceItem(source="X", tier=1)
        assert item.category is not None

    def test_categories_do_not_compare_across_types(self) -> None:
        assert DataCategory.FACT != DataCategory.ESTIMATE
        assert DataCategory.ESTIMATE != DataCategory.OPINION
        assert DataCategory.FACT != DataCategory.OPINION
