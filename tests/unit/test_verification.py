from __future__ import annotations

"""Unit tests for services/verification.py — cross-source verification.

Core rule under test: verification_status only flips to "verified" when a
second, independent source_type corroborates the same fact. An API
returning a value is never sufficient on its own.
"""

import uuid
from datetime import date

from investing_agent.schemas.corporate_actions import CorporateActionCreate
from investing_agent.schemas.financials import FinancialResultCreate
from investing_agent.services.verification import (
    verify_corporate_action_cross_source,
    verify_financial_result_cross_source,
)


def _action(source_type: str, ex_date: date, amount) -> CorporateActionCreate:
    return CorporateActionCreate(
        company_id=uuid.uuid4(),
        symbol="BEL",
        action_type="dividend",
        event_date=ex_date,
        ex_date=ex_date,
        amount=amount,
        source_type=source_type,
    )


class TestVerifyCorporateActionCrossSource:
    def test_matching_second_source_marks_verified(self) -> None:
        primary = _action("nse_json_hint", date(2026, 8, 13), 0.55)
        bse_candidate = _action("bse_json_hint", date(2026, 8, 13), 0.55)

        result = verify_corporate_action_cross_source(primary, [bse_candidate])

        assert result.verification_status == "verified"
        assert result.verification_method == "cross_source"
        assert result.verified_at is not None
        assert "bse_json_hint" in result.verification_notes

    def test_no_candidates_stays_unverified(self) -> None:
        primary = _action("nse_json_hint", date(2026, 8, 13), 0.55)
        result = verify_corporate_action_cross_source(primary, [])
        assert result.verification_status == "unverified"

    def test_same_source_type_does_not_self_verify(self) -> None:
        primary = _action("nse_json_hint", date(2026, 8, 13), 0.55)
        also_nse = _action("nse_json_hint", date(2026, 8, 13), 0.55)
        result = verify_corporate_action_cross_source(primary, [also_nse])
        assert result.verification_status == "unverified"

    def test_amount_mismatch_is_not_treated_as_a_match(self) -> None:
        """A genuine discrepancy (same date, different amount) must not be
        silently resolved into a false verification."""
        primary = _action("nse_json_hint", date(2026, 8, 13), 0.55)
        conflicting = _action("bse_json_hint", date(2026, 8, 13), 0.60)
        result = verify_corporate_action_cross_source(primary, [conflicting])
        assert result.verification_status == "unverified"

    def test_different_ex_date_does_not_match(self) -> None:
        primary = _action("nse_json_hint", date(2026, 8, 13), 0.55)
        different_event = _action("bse_json_hint", date(2026, 3, 6), 0.55)
        result = verify_corporate_action_cross_source(primary, [different_event])
        assert result.verification_status == "unverified"


def _financial(source_type: str, result_date: date, pat) -> FinancialResultCreate:
    return FinancialResultCreate(
        period_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        symbol="BEL",
        result_date=result_date,
        pat=pat,
        source_type=source_type,
    )


class TestVerifyFinancialResultCrossSource:
    def test_matching_second_source_marks_verified(self) -> None:
        primary = _financial("nse_json_hint", date(2025, 1, 30), 131606)
        bse_candidate = _financial("bse_json_hint", date(2025, 1, 30), 131606)
        result = verify_financial_result_cross_source(primary, [bse_candidate])
        assert result.verification_status == "verified"
        assert result.verification_method == "cross_source"

    def test_pat_mismatch_flags_discrepancy_not_verified(self) -> None:
        primary = _financial("nse_json_hint", date(2025, 1, 30), 131606)
        conflicting = _financial("bse_json_hint", date(2025, 1, 30), 999999)
        result = verify_financial_result_cross_source(primary, [conflicting])
        assert result.verification_status == "unverified"

    def test_no_candidates_stays_unverified(self) -> None:
        primary = _financial("nse_json_hint", date(2025, 1, 30), 131606)
        result = verify_financial_result_cross_source(primary, [])
        assert result.verification_status == "unverified"
