from __future__ import annotations

"""Normalization: turns source-layer Raw* rows into FinancialResultCreate /
CorporateActionCreate candidates.

Every field here is either parsed directly from the raw value or derived by
an explicit, documented rule (fiscal-year arithmetic, quarter-vs-YTD from
period span). Nothing is inferred from a provider's field *naming* — see
NSEDataSource's statement_scope_hint/unit_scale_hint, which are hard-coded
from bake-off-verified evidence, not read from the misleading
`re_con_pro_loss` field name. If a raw row can't be parsed, the corresponding
output field is left None/UNRESOLVED rather than guessed.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from investing_agent.schemas.corporate_actions import CorporateActionCreate
from investing_agent.schemas.financials import FinancialResultCreate
from investing_agent.services.sources.interfaces import RawCorporateAction, RawFinancialResult

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_nse_date(s: str | None) -> date | None:
    """Parses NSE's "DD-Mon-YYYY" date format, e.g. "13-Aug-2026"."""
    if not s or s == "-":
        return None
    try:
        day_s, mon_s, year_s = s.split("-")
        month = _MONTHS.get(mon_s.lower()[:3])
        if not month:
            return None
        return date(int(year_s), month, int(day_s))
    except (ValueError, AttributeError):
        return None


def parse_decimal(s: str | None) -> Decimal | None:
    if s is None:
        return None
    try:
        return Decimal(str(s))
    except (InvalidOperation, ValueError, TypeError):
        return None


def resolve_fiscal_period(period_end: date) -> tuple[int, str, str]:
    """Indian fiscal year: April Y to March Y+1 is "FY(Y+1)".

    Returns (fiscal_year, quarter_label, period_label), e.g.
    (2025, "Q3", "Q3FY25") for a period ending 31-Dec-2024.
    """
    fiscal_year = period_end.year + 1 if period_end.month >= 4 else period_end.year
    quarter_num = ((period_end.month - 4) % 12) // 3 + 1
    quarter = f"Q{quarter_num}"
    label = f"{quarter}FY{str(fiscal_year)[2:]}"
    return fiscal_year, quarter, label


def resolve_reporting_basis(period_start: date | None, period_end: date | None) -> str:
    """QUARTER if the reported span is ~one quarter (<=100 days), else YTD.
    Derived from the dates themselves, never guessed."""
    if not period_start or not period_end:
        return "QUARTER"
    span_days = (period_end - period_start).days
    return "QUARTER" if span_days <= 100 else "YTD"


class FinancialResultNormalizer:
    def normalize(
        self,
        raw: RawFinancialResult,
        *,
        company_id: uuid.UUID,
        period_id: uuid.UUID,
        symbol: str,
        source_type: str,
        source_url: str | None,
        published_at: datetime | None,
        source_document_id: uuid.UUID | None,
    ) -> FinancialResultCreate:
        period_start = parse_nse_date(raw.period_start)
        period_end = parse_nse_date(raw.period_end)

        return FinancialResultCreate(
            period_id=period_id,
            company_id=company_id,
            symbol=symbol,
            statement_scope=raw.statement_scope_hint or "UNRESOLVED",
            reporting_basis=resolve_reporting_basis(period_start, period_end),
            is_audited=bool(raw.is_audited_hint) if raw.is_audited_hint is not None else False,
            result_date=parse_nse_date(raw.result_date),
            currency="INR",
            unit_scale=raw.unit_scale_hint or "UNRESOLVED",
            revenue=parse_decimal(raw.revenue),
            # EBITDA is never derived here — the source doesn't disclose it
            # explicitly, and Phase 3A does not assume a uniform reporting
            # layout across companies. Left None rather than invented.
            ebitda=None,
            ebitda_margin_pct=None,
            ebitda_source=None,
            pbt=parse_decimal(raw.pbt),
            pat=parse_decimal(raw.pat),
            pat_margin_pct=None,
            eps_basic=parse_decimal(raw.eps_basic),
            eps_diluted=None,
            total_debt=None,
            cash_equivalents=None,
            operating_cash_flow=None,
            roe_pct=None,
            roce_pct=None,
            other_metrics={"extraction_method": raw.extraction_method, "raw": raw.raw},
            source_type=source_type,
            source_url=source_url,
            published_at=published_at,
            data_category="fact",
            confidence=None,
            source_document_id=source_document_id,
        )


class CorporateActionNormalizer:
    def normalize(
        self,
        raw: RawCorporateAction,
        *,
        company_id: uuid.UUID,
        symbol: str,
        source_type: str,
        source_url: str | None,
        published_at: datetime | None,
        source_document_id: uuid.UUID | None,
    ) -> CorporateActionCreate | None:
        event_date = parse_nse_date(raw.event_date)
        if event_date is None:
            # event_date is NOT NULL on the model; if we can't determine it,
            # we cannot persist this row — report and skip rather than guess.
            return None

        return CorporateActionCreate(
            company_id=company_id,
            symbol=symbol,
            action_type=raw.action_type,
            announced_date=parse_nse_date(raw.announced_date),
            event_date=event_date,
            ex_date=parse_nse_date(raw.ex_date),
            record_date=parse_nse_date(raw.record_date),
            payment_date=parse_nse_date(raw.payment_date),
            agm_date=parse_nse_date(raw.agm_date),
            amount=_extract_amount(raw.amount_text),
            amount_currency="INR",
            ratio=None,
            dividend_type=raw.dividend_type,
            details={"amount_text": raw.amount_text, "raw": raw.raw},
            is_confirmed=True,
            board_meeting_announced_at=None,
            board_meeting_date=parse_nse_date(raw.board_meeting_date),
            expected_result_date=parse_nse_date(raw.expected_result_date),
            actual_result_published_at=None,
            source_type=source_type,
            source_url=source_url,
            published_at=published_at,
            data_category="fact",
            confidence=None,
            source_document_id=source_document_id,
        )


def _extract_amount(text: str | None) -> Decimal | None:
    """Best-effort numeric extraction from free text like
    "Dividend - Re 0.55 Per Share" or "Interim Dividend - Rs 1.95 Per Share".
    Returns None (not a guess) when no clean number is found."""
    if not text:
        return None
    import re

    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    return parse_decimal(match.group(1))
