from __future__ import annotations

"""Unit tests for the pure ratio-parsing helper in
services/reconstruction/corporate_actions.py -- the rest of that module is
thin DB orchestration, covered by the integration suite."""

from decimal import Decimal

from investing_agent.services.reconstruction.corporate_actions import _parse_ratio


class TestParseRatio:
    def test_parses_valid_ratio(self) -> None:
        assert _parse_ratio("1:10") == (Decimal("1"), Decimal("10"))
        assert _parse_ratio("1:2") == (Decimal("1"), Decimal("2"))

    def test_handles_whitespace(self) -> None:
        assert _parse_ratio(" 1 : 5 ") == (Decimal("1"), Decimal("5"))

    def test_none_ratio_returns_none(self) -> None:
        assert _parse_ratio(None) is None

    def test_missing_colon_returns_none(self) -> None:
        assert _parse_ratio("bonus") is None

    def test_non_numeric_returns_none(self) -> None:
        assert _parse_ratio("one:ten") is None
