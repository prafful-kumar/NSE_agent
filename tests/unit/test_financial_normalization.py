from __future__ import annotations

"""Unit tests for services/normalization.py.

The most important test in this file is
test_bel_re_con_pro_loss_field_name_is_not_trusted — the regression test for
the bake-off finding that NSE's `re_con_pro_loss` field NAME implies
"consolidated" but its VALUE is BEL's standalone PAT. Normalization must
never derive statement_scope from field names; it comes only from the
adapter's explicit, evidence-based hint.
"""

import uuid
from datetime import date

from investing_agent.services.normalization import (
    CorporateActionNormalizer,
    FinancialResultNormalizer,
    parse_decimal,
    parse_nse_date,
    resolve_fiscal_period,
    resolve_reporting_basis,
)
from investing_agent.services.sources.interfaces import RawCorporateAction, RawFinancialResult


class TestParseNseDate:
    def test_valid_date(self) -> None:
        assert parse_nse_date("13-Aug-2026") == date(2026, 8, 13)

    def test_dash_placeholder(self) -> None:
        assert parse_nse_date("-") is None

    def test_none(self) -> None:
        assert parse_nse_date(None) is None

    def test_malformed(self) -> None:
        assert parse_nse_date("not-a-date") is None

    def test_empty_string(self) -> None:
        assert parse_nse_date("") is None


class TestParseDecimal:
    def test_valid_numeric_string(self) -> None:
        assert parse_decimal("575612") == 575612

    def test_none(self) -> None:
        assert parse_decimal(None) is None

    def test_garbage(self) -> None:
        assert parse_decimal("not-a-number") is None

    def test_negative(self) -> None:
        assert parse_decimal("-3956") == -3956


class TestResolveFiscalPeriod:
    def test_q3_oct_dec(self) -> None:
        fy, q, label = resolve_fiscal_period(date(2024, 12, 31))
        assert (fy, q, label) == (2025, "Q3", "Q3FY25")

    def test_q4_jan_mar(self) -> None:
        fy, q, label = resolve_fiscal_period(date(2025, 3, 31))
        assert (fy, q, label) == (2025, "Q4", "Q4FY25")

    def test_q1_apr_jun(self) -> None:
        fy, q, label = resolve_fiscal_period(date(2024, 6, 30))
        assert (fy, q, label) == (2025, "Q1", "Q1FY25")

    def test_q2_jul_sep(self) -> None:
        fy, q, label = resolve_fiscal_period(date(2024, 9, 30))
        assert (fy, q, label) == (2025, "Q2", "Q2FY25")


class TestResolveReportingBasis:
    def test_single_quarter_span(self) -> None:
        assert resolve_reporting_basis(date(2024, 10, 1), date(2024, 12, 31)) == "QUARTER"

    def test_nine_month_span_is_ytd(self) -> None:
        assert resolve_reporting_basis(date(2024, 4, 1), date(2024, 12, 31)) == "YTD"

    def test_missing_dates_default_to_quarter(self) -> None:
        assert resolve_reporting_basis(None, None) == "QUARTER"


class TestFinancialResultNormalizer:
    def test_bel_re_con_pro_loss_field_name_is_not_trusted(self) -> None:
        """The core regression test: raw.raw contains a field literally
        named re_con_pro_loss (reads as "consolidated"), but the verified,
        evidence-based hint says STANDALONE — normalization must honor the
        hint, not the field name."""
        raw = RawFinancialResult(
            period_start="01-OCT-2024",
            period_end="31-DEC-2024",
            result_date="30-JAN-2025",
            revenue="596120",
            pat="131606",
            pbt="175415",
            eps_basic="1.81",
            is_audited_hint=False,
            statement_scope_hint="STANDALONE",  # hard fact from bake-off, not from field name
            unit_scale_hint="LAKH",
            extraction_method="structured_api",
            raw={"re_con_pro_loss": "131606", "re_res_type": "U"},  # misleading name
        )
        norm = FinancialResultNormalizer().normalize(
            raw, company_id=uuid.uuid4(), period_id=uuid.uuid4(), symbol="BEL",
            source_type="nse_json_hint", source_url="https://x", published_at=None,
            source_document_id=None,
        )
        assert norm.statement_scope == "STANDALONE"
        assert norm.statement_scope != "CONSOLIDATED"
        assert norm.pat == parse_decimal("131606")
        # Matches BEL's own press release for Q3 FY24-25 standalone PAT (₹1,316.06 Cr)
        assert norm.pat == 131606

    def test_unresolved_scope_when_no_hint_given(self) -> None:
        raw = RawFinancialResult(
            period_start="01-OCT-2024", period_end="31-DEC-2024", result_date=None,
            revenue=None, pat=None, pbt=None, eps_basic=None, is_audited_hint=None,
            statement_scope_hint=None, unit_scale_hint=None,
            extraction_method="pdf_manual", raw={},
        )
        norm = FinancialResultNormalizer().normalize(
            raw, company_id=uuid.uuid4(), period_id=uuid.uuid4(), symbol="X",
            source_type="other", source_url=None, published_at=None, source_document_id=None,
        )
        assert norm.statement_scope == "UNRESOLVED"
        assert norm.unit_scale == "UNRESOLVED"

    def test_ebitda_never_invented(self) -> None:
        raw = RawFinancialResult(
            period_start="01-OCT-2024", period_end="31-DEC-2024", result_date=None,
            revenue="596120", pat="131606", pbt="175415", eps_basic="1.81",
            is_audited_hint=False, statement_scope_hint="STANDALONE",
            unit_scale_hint="LAKH", extraction_method="structured_api", raw={},
        )
        norm = FinancialResultNormalizer().normalize(
            raw, company_id=uuid.uuid4(), period_id=uuid.uuid4(), symbol="BEL",
            source_type="nse_json_hint", source_url=None, published_at=None,
            source_document_id=None,
        )
        assert norm.ebitda is None
        assert norm.ebitda_source is None

    def test_verification_status_defaults_unverified(self) -> None:
        raw = RawFinancialResult(
            period_start="01-OCT-2024", period_end="31-DEC-2024", result_date=None,
            revenue="1", pat="1", pbt="1", eps_basic="1", is_audited_hint=True,
            statement_scope_hint="STANDALONE", unit_scale_hint="LAKH",
            extraction_method="structured_api", raw={},
        )
        norm = FinancialResultNormalizer().normalize(
            raw, company_id=uuid.uuid4(), period_id=uuid.uuid4(), symbol="BEL",
            source_type="nse_json_hint", source_url=None, published_at=None,
            source_document_id=None,
        )
        assert norm.verification_status == "unverified"

    def test_is_audited_defaults_false_when_hint_missing(self) -> None:
        raw = RawFinancialResult(
            period_start="01-OCT-2024", period_end="31-DEC-2024", result_date=None,
            revenue=None, pat=None, pbt=None, eps_basic=None, is_audited_hint=None,
            statement_scope_hint=None, unit_scale_hint=None,
            extraction_method="pdf_manual", raw={},
        )
        norm = FinancialResultNormalizer().normalize(
            raw, company_id=uuid.uuid4(), period_id=uuid.uuid4(), symbol="X",
            source_type="other", source_url=None, published_at=None, source_document_id=None,
        )
        assert norm.is_audited is False


class TestCorporateActionNormalizer:
    def test_dividend_amount_extracted_from_free_text(self) -> None:
        raw = RawCorporateAction(
            action_type="dividend", announced_date=None, event_date="13-Aug-2026",
            ex_date="13-Aug-2026", record_date="13-Aug-2026", payment_date=None,
            agm_date=None, amount_text="Dividend - Re 0.55 Per Share",
            dividend_type=None, board_meeting_announced_at=None,
            board_meeting_date=None, expected_result_date=None,
            actual_result_published_at=None, raw={},
        )
        norm = CorporateActionNormalizer().normalize(
            raw, company_id=uuid.uuid4(), symbol="BEL", source_type="nse_json_hint",
            source_url=None, published_at=None, source_document_id=None,
        )
        assert norm is not None
        assert norm.amount == parse_decimal("0.55")
        assert norm.payment_date is None  # never inferred

    def test_interim_dividend_type_detected(self) -> None:
        raw = RawCorporateAction(
            action_type="dividend", announced_date=None, event_date="06-Mar-2026",
            ex_date="06-Mar-2026", record_date="06-Mar-2026", payment_date=None,
            agm_date=None, amount_text="Interim Dividend - Rs 1.95 Per Share",
            dividend_type="interim", board_meeting_announced_at=None,
            board_meeting_date=None, expected_result_date=None,
            actual_result_published_at=None, raw={},
        )
        norm = CorporateActionNormalizer().normalize(
            raw, company_id=uuid.uuid4(), symbol="BEL", source_type="nse_json_hint",
            source_url=None, published_at=None, source_document_id=None,
        )
        assert norm.dividend_type == "interim"

    def test_ambiguous_dividend_type_not_guessed(self) -> None:
        raw = RawCorporateAction(
            action_type="dividend", announced_date=None, event_date="14-Aug-2025",
            ex_date="14-Aug-2025", record_date="14-Aug-2025", payment_date=None,
            agm_date=None, amount_text="Dividend - Rs 0.90 Per Share",
            dividend_type=None, board_meeting_announced_at=None,
            board_meeting_date=None, expected_result_date=None,
            actual_result_published_at=None, raw={},
        )
        norm = CorporateActionNormalizer().normalize(
            raw, company_id=uuid.uuid4(), symbol="BEL", source_type="nse_json_hint",
            source_url=None, published_at=None, source_document_id=None,
        )
        assert norm.dividend_type is None  # not assumed to be "final"

    def test_missing_event_date_skips_row(self) -> None:
        raw = RawCorporateAction(
            action_type="dividend", announced_date=None, event_date=None,
            ex_date=None, record_date=None, payment_date=None,
            agm_date=None, amount_text="Dividend - Rs 1 Per Share",
            dividend_type=None, board_meeting_announced_at=None,
            board_meeting_date=None, expected_result_date=None,
            actual_result_published_at=None, raw={},
        )
        norm = CorporateActionNormalizer().normalize(
            raw, company_id=uuid.uuid4(), symbol="BEL", source_type="nse_json_hint",
            source_url=None, published_at=None, source_document_id=None,
        )
        assert norm is None
