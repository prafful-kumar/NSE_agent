from __future__ import annotations

"""Schemas for financial_periods / financial_results.

statement_scope and unit_scale intentionally default to UNRESOLVED rather
than STANDALONE/ACTUAL — normalization must prove the scope from the source
document/adapter, never guess (see the BEL re_con_pro_loss regression test).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from investing_agent.schemas.common import BaseSchema, TimestampedSchema

PeriodType = Literal["quarter", "annual"]
StatementScope = Literal["STANDALONE", "CONSOLIDATED", "UNRESOLVED"]
ReportingBasis = Literal["QUARTER", "YTD", "ANNUAL"]
UnitScale = Literal["LAKH", "CRORE", "ACTUAL", "UNRESOLVED"]
EbitdaSource = Literal["reported", "derived"]


class FinancialPeriodRead(TimestampedSchema):
    id: uuid.UUID
    company_id: uuid.UUID
    period_type: str
    fiscal_year: int
    quarter: str | None
    period_start: date | None
    period_end: date
    label: str


class FinancialResultCreate(BaseSchema):
    """Internal, used by the normalization/ingestion service — not a public API input."""

    period_id: uuid.UUID
    company_id: uuid.UUID
    symbol: str
    statement_scope: StatementScope = "UNRESOLVED"
    reporting_basis: ReportingBasis = "QUARTER"
    is_audited: bool = False
    result_date: date | None = None
    currency: str = "INR"
    unit_scale: UnitScale = "UNRESOLVED"
    revenue: Decimal | None = None
    ebitda: Decimal | None = None
    ebitda_margin_pct: Decimal | None = None
    ebitda_source: EbitdaSource | None = None
    pbt: Decimal | None = None
    pat: Decimal | None = None
    pat_margin_pct: Decimal | None = None
    eps_basic: Decimal | None = None
    eps_diluted: Decimal | None = None
    total_debt: Decimal | None = None
    cash_equivalents: Decimal | None = None
    operating_cash_flow: Decimal | None = None
    roe_pct: Decimal | None = None
    roce_pct: Decimal | None = None
    other_metrics: dict[str, Any] | None = None
    source_type: str
    source_url: str | None = None
    published_at: datetime | None = None
    data_category: str = "fact"
    confidence: float | None = None
    source_document_id: uuid.UUID | None = None
    verification_status: str = "unverified"
    verification_document_id: uuid.UUID | None = None
    verified_at: datetime | None = None
    verification_method: str | None = None
    verification_notes: str | None = None


class FinancialResultRead(TimestampedSchema):
    id: uuid.UUID
    period_id: uuid.UUID
    company_id: uuid.UUID
    symbol: str
    statement_scope: str
    reporting_basis: str
    is_audited: bool
    result_date: date | None
    currency: str
    unit_scale: str
    revenue: Decimal | None
    ebitda: Decimal | None
    ebitda_margin_pct: Decimal | None
    ebitda_source: str | None
    pbt: Decimal | None
    pat: Decimal | None
    pat_margin_pct: Decimal | None
    eps_basic: Decimal | None
    eps_diluted: Decimal | None
    total_debt: Decimal | None
    cash_equivalents: Decimal | None
    operating_cash_flow: Decimal | None
    roe_pct: Decimal | None
    roce_pct: Decimal | None
    version: int
    is_latest: bool
    source_type: str
    source_url: str | None
    published_at: datetime | None
    available_at: datetime
    data_category: str
    verification_status: str
    verification_method: str | None
    verified_at: datetime | None
