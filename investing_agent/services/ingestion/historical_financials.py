from __future__ import annotations

"""Manual/deterministic transcription of historical quarterly results from
an already-archived primary document (NSE filing PDF), for quarters older
than the structured NSE results-comparison API's 5-quarter window.

This is NOT an LLM extraction path. A human reads the archived document via
the Read tool, cross-checks the figures against the primary source's own
P&L table, and supplies them here. The row is created already
verification_status="verified" — there is no separate "unverified from an
API, later confirmed" step for these quarters, because there is no second
independent source to compare against; the primary document itself is the
source, and reading it IS the verification act (verification_method
"manual_primary_document", same value used by verify_financial_result_manual
for the same reason).

If an LLM-assisted extractor is built later, it must write to a staging
table (mirroring ExtractionCandidate) and require the same explicit human
verification — never call this function directly with LLM-derived values.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import FinancialResult
from investing_agent.db.repositories.financial import (
    FinancialPeriodRepository,
    FinancialResultRepository,
)
from investing_agent.db.repositories.source_document import SourceDocumentRepository
from investing_agent.schemas.financials import FinancialResultCreate


class HistoricalFinancialTranscriptionError(ValueError):
    pass


@dataclass
class HistoricalFinancialResult:
    row: FinancialResult
    was_new_version: bool


async def record_historical_financial_result(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    symbol: str,
    fiscal_year: int,
    quarter: str,
    period_end: date,
    period_start: date | None,
    period_label: str,
    statement_scope: str,
    reporting_basis: str,
    is_audited: bool,
    result_date: date | None,
    unit_scale: str,
    source_document_id: uuid.UUID,
    source_page: int | None,
    source_quote: str | None,
    revenue: Decimal | None = None,
    pbt: Decimal | None = None,
    pat: Decimal | None = None,
    eps_basic: Decimal | None = None,
    eps_diluted: Decimal | None = None,
    tax_expense: Decimal | None = None,
    changes_in_inventories: Decimal | None = None,
    employee_cost: Decimal | None = None,
    finance_cost: Decimal | None = None,
    other_expenses: Decimal | None = None,
) -> HistoricalFinancialResult:
    doc = await SourceDocumentRepository(session).get(source_document_id)
    if doc is None:
        raise HistoricalFinancialTranscriptionError(
            f"source_document {source_document_id} not found"
        )
    if doc.company_id != company_id:
        raise HistoricalFinancialTranscriptionError(
            "source_document belongs to a different company"
        )
    if doc.source_type == "nse_json_hint":
        raise HistoricalFinancialTranscriptionError(
            "cannot transcribe from an undocumented JSON hint — provide an "
            "archived primary filing/document instead"
        )

    period_repo = FinancialPeriodRepository(session)
    period = await period_repo.get_or_create(
        company_id=company_id,
        period_type="quarter",
        fiscal_year=fiscal_year,
        quarter=quarter,
        period_end=period_end,
        period_start=period_start,
        label=period_label,
    )

    other_metrics: dict[str, object] = {"extraction_method": "pdf_manual"}
    if source_page is not None:
        other_metrics["source_page"] = source_page
    if source_quote is not None:
        other_metrics["source_quote"] = source_quote
    extra_fields = {
        "tax_expense": tax_expense,
        "changes_in_inventories": changes_in_inventories,
        "employee_cost": employee_cost,
        "finance_cost": finance_cost,
        "other_expenses": other_expenses,
    }
    for key, value in extra_fields.items():
        if value is not None:
            other_metrics[key] = str(value)

    now = datetime.now(UTC)
    data = FinancialResultCreate(
        period_id=period.id,
        company_id=company_id,
        symbol=symbol,
        statement_scope=statement_scope,
        reporting_basis=reporting_basis,
        is_audited=is_audited,
        result_date=result_date,
        unit_scale=unit_scale,
        revenue=revenue,
        pbt=pbt,
        pat=pat,
        eps_basic=eps_basic,
        eps_diluted=eps_diluted,
        other_metrics=other_metrics,
        source_type="nse_filing_pdf",
        source_url=doc.source_url,
        published_at=doc.published_at,
        available_at=doc.published_at,
        timestamp_precision="EXACT" if doc.published_at is not None else "DATE_ONLY",
        data_category="fact",
        source_document_id=source_document_id,
        verification_status="verified",
        verification_document_id=source_document_id,
        verified_at=now,
        verification_method="manual_primary_document",
        verification_notes=(
            f"Manually transcribed from archived primary document {source_document_id} "
            f"(page {source_page})." if source_page is not None
            else f"Manually transcribed from archived primary document {source_document_id}."
        ),
    )

    result_repo = FinancialResultRepository(session)
    row, was_new = await result_repo.upsert_versioned(data)
    return HistoricalFinancialResult(row=row, was_new_version=was_new)
