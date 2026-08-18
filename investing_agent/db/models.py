from __future__ import annotations
"""SQLAlchemy ORM models for the investing agent.

Every table includes created_at, updated_at, source, source_timestamp,
and ingestion_timestamp on records that represent ingested external data.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Timestamp mixin ────────────────────────────────────────────────────────────

class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SourceMixin:
    """For records that come from an external source."""
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingestion_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProvenanceMixin:
    """Full provenance trail for Phase 3+ research data (filings, financials,
    corporate actions, company events).

    available_at is distinct from published_at: published_at is when the
    source (NSE/BSE/company) says the information became public; available_at
    is when it became queryable in *our* system. Backtests must filter on
    available_at, never published_at, to avoid look-ahead bias — a filing
    published at 4pm might not be ingested until the next morning's batch run.
    """
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    data_category: Mapped[str] = mapped_column(String(20), nullable=False, default="fact")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))


class VerificationMixin:
    """Document-verification trail for normalized facts (CorporateAction,
    FinancialResult).

    data_category (from ProvenanceMixin) says what KIND of claim this is
    (FACT vs ESTIMATE vs OPINION). verification_status says whether WE have
    confirmed it against an archived source document. A row can be
    data_category="fact" (it purports to be an objective figure) while
    verification_status="unverified" (we only have it from an undocumented
    JSON hint, not yet reconciled against the filing PDF/XBRL) — the two are
    deliberately decoupled. Ingestion must never flip verification_status to
    "verified" just because an API returned a value; only a successful
    reconciliation against source_documents does that.
    """

    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_documents.id"), index=True
    )
    verification_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unverified", index=True
    )
    verification_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_documents.id")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # manual|structured_xbrl|parser|cross_source
    verification_method: Mapped[str | None] = mapped_column(String(30))
    verification_notes: Mapped[str | None] = mapped_column(Text)


class ExtractionMixin:
    """Extraction/verification trail for Phase 3B primary-source facts
    (order book, guidance, segment/operational metrics, capacity updates,
    management commentary).

    Deliberately a separate field name/vocabulary from VerificationMixin
    (used by CorporateAction/FinancialResult), not reused — these are
    human-entered point observations transcribed from a primary document,
    not exchange filings reconciled across sources. Keeping the vocabulary
    distinct means Phase 3A's cross-source verification logic
    (services/verification.py) never has to reason about a status value it
    wasn't designed for.

    extraction_method: MANUAL|DETERMINISTIC|LLM_ASSISTED|HYBRID.
    LLM_ASSISTED exists in the vocabulary for a future extractor but nothing
    in Phase 3B writes it — every Phase 3B write path is MANUAL (CLI entry)
    or DETERMINISTIC (mechanical PDF text extraction feeding a human-reviewed
    CLI entry). See ExtractionCandidate for the staging table a future
    LLM-assisted extractor must write to instead of directly here.

    verification_status: UNVERIFIED|HUMAN_VERIFIED|DOCUMENT_VERIFIED|REJECTED.
    Defaults to UNVERIFIED; only flips on an explicit human action (CLI
    --verify flag), never automatically.
    """

    source_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_documents.id"), index=True, nullable=False
    )
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_section: Mapped[str | None] = mapped_column(String(255))
    source_quote: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_method: Mapped[str] = mapped_column(String(20), nullable=False, default="MANUAL")
    extractor_version: Mapped[str | None] = mapped_column(String(50))
    verification_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="UNVERIFIED", index=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[str | None] = mapped_column(String(100))


# ── Companies ──────────────────────────────────────────────────────────────────

class Company(TimestampMixin, Base):
    """Canonical company entity. The anchor for all other data."""

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    symbol: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    isin: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False)  # NSE | BSE
    sector: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(100))
    market_cap_category: Mapped[str | None] = mapped_column(String(20))  # Large|Mid|Small
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)

    # ── Exchange / filing identity (Phase 3) ────────────────────────────────────
    nse_symbol: Mapped[str | None] = mapped_column(String(30), index=True)
    bse_code: Mapped[str | None] = mapped_column(String(20), index=True)  # numeric scrip code
    cin: Mapped[str | None] = mapped_column(String(30))  # Corporate Identification Number
    website: Mapped[str | None] = mapped_column(Text)
    filing_identifiers: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    identifiers_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    holdings: Mapped[list["Holding"]] = relationship(back_populates="company")
    watchlist_items: Mapped[list["WatchlistItem"]] = relationship(back_populates="company")
    investment_theses: Mapped[list["InvestmentThesis"]] = relationship(
        back_populates="company"
    )
    corporate_actions: Mapped[list["CorporateAction"]] = relationship(back_populates="company")
    company_events: Mapped[list["CompanyEvent"]] = relationship(back_populates="company")
    financial_periods: Mapped[list["FinancialPeriod"]] = relationship(back_populates="company")
    source_documents: Mapped[list["SourceDocument"]] = relationship(back_populates="company")

    def __repr__(self) -> str:
        return f"<Company {self.symbol}>"


# ── Portfolio snapshots ────────────────────────────────────────────────────────

class PortfolioSnapshot(TimestampMixin, SourceMixin, Base):
    """Daily portfolio state snapshot.  One row per sync from the broker."""

    __tablename__ = "portfolio_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    total_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    total_invested: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    total_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    holdings: Mapped[list["Holding"]] = relationship(back_populates="snapshot")

    __table_args__ = (
        UniqueConstraint("user_id", "snapshot_date", "source", name="uq_snapshot_user_date_src"),
    )


class Holding(TimestampMixin, Base):
    """Individual stock holding within a portfolio snapshot."""

    __tablename__ = "holdings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    isin: Mapped[str | None] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    average_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    current_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    portfolio_weight_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    t1_quantity: Mapped[int | None] = mapped_column(BigInteger)  # Unsettled

    snapshot: Mapped["PortfolioSnapshot"] = relationship(back_populates="holdings")
    company: Mapped["Company | None"] = relationship(back_populates="holdings")


# ── Watchlist ─────────────────────────────────────────────────────────────────

class WatchlistItem(TimestampMixin, Base):
    __tablename__ = "watchlist"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    company: Mapped["Company | None"] = relationship(back_populates="watchlist_items")

    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),
    )


# ── User preferences / Investor profile ───────────────────────────────────────

class UserPreference(TimestampMixin, Base):
    __tablename__ = "user_preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    preferred_sectors: Mapped[list[str] | None] = mapped_column(JSONB)
    avoided_sectors: Mapped[list[str] | None] = mapped_column(JSONB)
    avoided_stocks: Mapped[list[str] | None] = mapped_column(JSONB)
    preferred_holding_period_months: Mapped[int | None] = mapped_column(Integer)
    risk_tolerance: Mapped[str | None] = mapped_column(String(20))  # low|medium|high
    max_stock_allocation_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    max_sector_allocation_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    min_market_cap_category: Mapped[str | None] = mapped_column(String(20))
    valuation_preference: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)


# ── Investment theses ─────────────────────────────────────────────────────────

class InvestmentThesis(TimestampMixin, Base):
    """Active or historical investment thesis for a stock."""

    __tablename__ = "investment_theses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # active|watching|exited|avoided
    thesis: Mapped[str | None] = mapped_column(Text)
    buy_reasons: Mapped[list[str] | None] = mapped_column(JSONB)
    risk_factors: Mapped[list[str] | None] = mapped_column(JSONB)
    catalysts: Mapped[list[str] | None] = mapped_column(JSONB)
    invalidation_conditions: Mapped[list[str] | None] = mapped_column(JSONB)
    target_price_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    target_price_base: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    target_price_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    horizon_months: Mapped[int | None] = mapped_column(Integer)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    outcome_notes: Mapped[str | None] = mapped_column(Text)
    # Vector embedding of the thesis text (for semantic retrieval)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))

    company: Mapped["Company | None"] = relationship(back_populates="investment_theses")


# ── Corporate actions (dividend/bonus/split/buyback/AGM/result date) ─────────

class CorporateAction(TimestampMixin, ProvenanceMixin, VerificationMixin, Base):
    """Corporate actions sourced from NSE/BSE exchange filings — Tier-1 FACT
    once verification_status="verified" (see VerificationMixin).

    Never infer payment_date; only store when the exchange/company publishes it.
    Versioned: a revised dividend amount or postponed AGM creates a new version
    row with is_latest=True and the prior row's is_latest flipped to False —
    old values are never overwritten or deleted (audit requirement).

    board_meeting_date and actual_result_published_at are deliberately
    separate columns, never conflated: a board meeting can be announced,
    held, and only publish results hours (or days) later.
    """

    __tablename__ = "corporate_actions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # dividend|bonus|split|buyback|rights|agm|board_meeting|other
    action_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    announced_date: Mapped[date | None] = mapped_column(Date)
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    ex_date: Mapped[date | None] = mapped_column(Date, index=True)
    record_date: Mapped[date | None] = mapped_column(Date)
    # Payment date is NOT inferred; only stored when published
    payment_date: Mapped[date | None] = mapped_column(Date)
    agm_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    amount_currency: Mapped[str | None] = mapped_column(String(10))
    ratio: Mapped[str | None] = mapped_column(String(50))  # e.g. "1:2" for bonus
    dividend_type: Mapped[str | None] = mapped_column(String(20))  # final|interim|special
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── Board meeting / result calendar (kept distinct on purpose) ─────────────
    board_meeting_announced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    board_meeting_date: Mapped[date | None] = mapped_column(Date)
    expected_result_date: Mapped[date | None] = mapped_column(Date)
    actual_result_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Cross-verification between NSE and BSE ──────────────────────────────────
    cross_checked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cross_check_source: Mapped[str | None] = mapped_column(String(50))
    cross_check_mismatch: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # ── Versioning (amendments / corrections) ────────────────────────────────────
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corporate_actions.id")
    )

    company: Mapped["Company"] = relationship(back_populates="corporate_actions")

    __table_args__ = (
        UniqueConstraint(
            "company_id", "action_type", "event_date", "source_type", "version",
            name="uq_corpaction_company_type_date_src_ver",
        ),
    )


# ── Generic qualitative company events (non-financial timeline) ──────────────

class CompanyEvent(TimestampMixin, ProvenanceMixin, Base):
    """Qualitative timeline events that don't fit the strict corporate-action
    schema: management changes, credit rating actions, litigation, regulatory
    orders, order wins, capacity expansion, scheduled concalls, etc.
    """

    __tablename__ = "company_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    company: Mapped["Company"] = relationship(back_populates="company_events")

    __table_args__ = (
        UniqueConstraint(
            "company_id", "event_type", "event_date", "content_hash",
            name="uq_company_event_dedup",
        ),
    )


# ── Financial periods (dimension: which quarter/year a result belongs to) ────

class FinancialPeriod(TimestampMixin, Base):
    """Defines a reporting period. Purely a dimension table — no financial
    values live here (see FinancialResult), so it never needs versioning.
    """

    __tablename__ = "financial_periods"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True, nullable=False
    )
    period_type: Mapped[str] = mapped_column(String(10), nullable=False)  # quarter|annual
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)  # e.g. 2025 = FY25
    quarter: Mapped[str | None] = mapped_column(String(5))  # Q1|Q2|Q3|Q4, None for annual
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    label: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g. "Q2FY25"

    company: Mapped["Company"] = relationship(back_populates="financial_periods")
    results: Mapped[list["FinancialResult"]] = relationship(back_populates="period")

    __table_args__ = (
        UniqueConstraint(
            "company_id", "period_type", "fiscal_year", "quarter",
            name="uq_finperiod_company_type_fy_q",
        ),
    )


# ── Financial results (fact table: the actual reported numbers) ─────────────

class FinancialResult(TimestampMixin, ProvenanceMixin, VerificationMixin, Base):
    """Reported financial results for a period. Prefer XBRL/structured data
    over PDF-derived values whenever both are available (tracked via
    extraction_method in other_metrics).

    statement_scope/reporting_basis are explicit, never inferred from a
    provider's field naming: NSE's `re_con_pro_loss` field name implies
    "consolidated" but was empirically found (BEL Q3 FY24-25) to hold the
    STANDALONE PAT — see tests/unit/test_financial_normalization.py for the
    regression test. UNRESOLVED is a legitimate value when the source
    document doesn't make the scope determinable; normalization must never
    guess to fill it in.

    Versioned: restated/revised results (common after audit finalisation)
    create a new row with is_latest=True; the previous row's is_latest flips
    to False. Nothing is ever overwritten — this is required for point-in-time
    correct backtesting (a backtest run "as of" a date must see the numbers
    that were actually available then, not a later restatement).
    """

    __tablename__ = "financial_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("financial_periods.id"), index=True, nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # STANDALONE|CONSOLIDATED|UNRESOLVED — never guessed from field names
    statement_scope: Mapped[str] = mapped_column(
        String(20), nullable=False, default="UNRESOLVED"
    )
    # QUARTER|YTD|ANNUAL
    reporting_basis: Mapped[str] = mapped_column(String(10), nullable=False, default="QUARTER")
    is_audited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    result_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="INR")
    # LAKH|CRORE|ACTUAL|UNRESOLVED — never guessed
    unit_scale: Mapped[str] = mapped_column(String(10), nullable=False, default="UNRESOLVED")

    # Revenue / Topline
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    # EBITDA — only populated when explicitly disclosed or deterministically
    # derivable from disclosed line items; ebitda_source records which.
    ebitda: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    ebitda_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    ebitda_source: Mapped[str | None] = mapped_column(String(10))  # reported|derived
    # PBT / PAT
    pbt: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    pat: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    pat_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    # EPS
    eps_basic: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    eps_diluted: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    # Balance sheet
    total_debt: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    cash_equivalents: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    # Cash flow
    operating_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    # Returns
    roe_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    roce_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    # Other / extraction_method: "xbrl" | "structured_api" | "pdf_manual" | "mock"
    other_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # ── Versioning (restatements) ────────────────────────────────────────────
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("financial_results.id")
    )

    period: Mapped["FinancialPeriod"] = relationship(back_populates="results")

    __table_args__ = (
        UniqueConstraint(
            "period_id", "statement_scope", "reporting_basis", "source_type", "version",
            name="uq_finresult_period_scope_basis_src_ver",
        ),
    )


# ── Source documents (raw archive; never overwritten) ────────────────────────

class SourceDocument(TimestampMixin, ProvenanceMixin, Base):
    """Raw downloaded/discovered document, stored byte-for-byte unchanged —
    the Tier-1 ground truth. Every distinct (company, source_url,
    content_hash) combination is one row; re-ingesting the same URL with
    unchanged bytes is a no-op (idempotent), not a new row. Normalization
    into CorporateAction/FinancialResult happens only after the row exists
    here — see VerificationMixin.source_document_id.

    Amendment/correction chains are NOT tracked on this table (no
    amends_document_id here) — that lives exclusively in DocumentVersion, so
    there's one place to look for "what version was this" rather than two.
    """

    __tablename__ = "source_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False)  # NSE|BSE|IR
    # quarterly_result|annual_report|investor_presentation|announcement|
    # concall_transcript|other
    filing_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    document_type: Mapped[str] = mapped_column(String(20), nullable=False)  # pdf|html|xml|xbrl|json
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    period_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("financial_periods.id"), index=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # sha256
    storage_path: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    company: Mapped["Company"] = relationship(back_populates="source_documents")
    versions: Mapped[list["DocumentVersion"]] = relationship(back_populates="source_document")

    __table_args__ = (
        UniqueConstraint(
            "company_id", "source_url", "content_hash",
            name="uq_sourcedoc_company_url_hash",
        ),
    )


# ── Document versions (explicit amendment/correction chain) ─────────────────

class DocumentVersion(TimestampMixin, Base):
    """Explicit version chain for a source_document. Created once (version 1,
    status="original") for every new SourceDocument; a later amendment
    creates a *new* SourceDocument row plus a new DocumentVersion row
    (version 2+), and the prior version's superseded_by_id is set to point
    forward — the full correction history is always reconstructable without
    ever deleting or overwriting a row.
    """

    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_documents.id"), index=True, nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # original|amended|corrected|withdrawn
    effective_from: Mapped[date | None] = mapped_column(Date)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id")
    )
    notes: Mapped[str | None] = mapped_column(Text)

    source_document: Mapped["SourceDocument"] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint(
            "source_document_id", "version_number", name="uq_docversion_srcdoc_num"
        ),
    )


# ── Ingestion runs (audit trail) ──────────────────────────────────────────────

class IngestionRun(TimestampMixin, Base):
    """Audit trail for every data ingestion operation."""

    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # portfolio|fundamentals|news|corporate_actions|...
    status: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # running|success|failed|partial
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    symbols: Mapped[list[str] | None] = mapped_column(JSONB)
    records_ingested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("run_metadata", JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ── Recommendations ────────────────────────────────────────────────────────────

class Recommendation(TimestampMixin, Base):
    """Agent-generated investment recommendation with full evidence trail."""

    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # BUY|ADD|HOLD|REDUCE|AVOID|WATCH|INSUFFICIENT_EVIDENCE
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    horizon: Mapped[str | None] = mapped_column(String(50))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    thesis_status: Mapped[str | None] = mapped_column(String(30))  # intact|improving|weakening|broken
    fair_value_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    fair_value_base: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    fair_value_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    reasons: Mapped[list[str] | None] = mapped_column(JSONB)
    risks: Mapped[list[str] | None] = mapped_column(JSONB)
    invalidation_conditions: Mapped[list[str] | None] = mapped_column(JSONB)
    upcoming_events: Mapped[list[str] | None] = mapped_column(JSONB)
    evidence: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    data_freshness: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Prompt and model version for reproducibility
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    model_version: Mapped[str | None] = mapped_column(String(100))
    # Raw LLM output (never remove — audit requirement)
    raw_llm_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    human_approved: Mapped[bool | None] = mapped_column(Boolean)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ── Instrument master ─────────────────────────────────────────────────────────

class InstrumentMaster(TimestampMixin, Base):
    """Maps broker instrument identifiers to internal company_id.

    ISIN is the durable identity.  Broker tokens and trading symbols can change.
    Resolution priority: ISIN → symbol+exchange → create new company stub.
    """

    __tablename__ = "instrument_master"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tradingsymbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False)  # NSE | BSE
    isin: Mapped[str | None] = mapped_column(String(20), index=True)
    # EQ | ETF | MF | BE | BL | GB | GS | INDEX | OTHER
    instrument_type: Mapped[str] = mapped_column(String(20), nullable=False, default="EQ")
    zerodha_instrument_token: Mapped[int | None] = mapped_column(BigInteger, index=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped["Company | None"] = relationship()

    __table_args__ = (
        UniqueConstraint("tradingsymbol", "exchange", name="uq_instrument_symbol_exchange"),
    )


# ── Phase 3B: primary-source company intelligence ────────────────────────────
# Every table below is a human-entered point observation transcribed from an
# archived primary document (investor presentation, annual report, concall
# transcript), never an automated fact. No version-chain machinery
# (is_latest/supersedes_id) — unlike CorporateAction/FinancialResult, these
# aren't corrected exchange filings; if a real correction case shows up later,
# versioning can be added then. See ExtractionMixin above for the shared
# provenance/verification fields.


class OrderBookSnapshot(TimestampMixin, ProvenanceMixin, ExtractionMixin, Base):
    """A disclosed order-book value as of a point in time. expected_execution_period
    is kept as free text (e.g. "over the next 3-4 years") and never parsed into
    dates — companies rarely give a precise date, and inventing one would be a
    fabrication."""

    __tablename__ = "order_book_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    order_book_value: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="INR")
    unit_scale: Mapped[str] = mapped_column(String(10), nullable=False, default="UNRESOLVED")
    segment: Mapped[str | None] = mapped_column(String(100))
    book_to_bill_ratio: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    expected_execution_period: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)


class ManagementGuidance(TimestampMixin, ProvenanceMixin, ExtractionMixin, Base):
    """Forward-looking guidance as stated by management. guidance_value_text
    always retains the verbatim wording (e.g. "15-18% revenue growth");
    guidance_low/high are only populated when the source gives an explicit
    numeric range — never back-derived from prose."""

    __tablename__ = "management_guidance"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # revenue|margin|order_inflow|capex|other
    guidance_type: Mapped[str] = mapped_column(String(50), nullable=False)
    metric_label: Mapped[str] = mapped_column(String(255), nullable=False)
    guidance_value_text: Mapped[str] = mapped_column(Text, nullable=False)
    guidance_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    guidance_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    period_label: Mapped[str | None] = mapped_column(String(50))  # e.g. "FY26"
    given_by: Mapped[str | None] = mapped_column(String(255))  # e.g. "CMD" / "CFO"
    context: Mapped[str | None] = mapped_column(Text)


class SegmentMetric(TimestampMixin, ProvenanceMixin, ExtractionMixin, Base):
    """A segment-level (business-line/geography) metric disclosed alongside or
    separately from consolidated financials. period_id links to
    financial_periods when the disclosure ties to a specific reporting
    period; nullable because some segment data (e.g. an investor-day slide)
    isn't tied to a single quarter/year."""

    __tablename__ = "segment_metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    period_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("financial_periods.id"), index=True
    )
    segment_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # revenue|ebit|order_book|other
    metric_type: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    unit_scale: Mapped[str] = mapped_column(String(10), nullable=False, default="UNRESOLVED")
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="INR")


class OperationalMetric(TimestampMixin, ProvenanceMixin, ExtractionMixin, Base):
    """Non-financial operating metrics disclosed by the company (e.g. capacity
    utilization %, units produced, headcount). unit is free text (e.g. "%",
    "units", "MW") — never assumed."""

    __tablename__ = "operational_metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    period_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("financial_periods.id"), index=True
    )
    metric_name: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(50))
    as_of_date: Mapped[date | None] = mapped_column(Date, index=True)


class CapacityUpdate(TimestampMixin, ProvenanceMixin, ExtractionMixin, Base):
    """A disclosed change in manufacturing/production capacity. expected_completion
    is free text, never parsed to a date, for the same reason as
    OrderBookSnapshot.expected_execution_period."""

    __tablename__ = "capacity_updates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # expansion|new_facility|other
    update_type: Mapped[str] = mapped_column(String(50), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))
    capacity_before: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    capacity_after: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    unit: Mapped[str | None] = mapped_column(String(50))
    announced_date: Mapped[date | None] = mapped_column(Date, index=True)
    expected_completion: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)


class ManagementCommentary(TimestampMixin, ProvenanceMixin, ExtractionMixin, Base):
    """A direct quote or paraphrase from management (concall/AGM/press),
    kept close to verbatim. quote is required — this table exists specifically
    to carry the evidentiary text, not just a category label."""

    __tablename__ = "management_commentary"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    period_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("financial_periods.id"), index=True
    )
    speaker: Mapped[str | None] = mapped_column(String(255))
    topic: Mapped[str | None] = mapped_column(String(255))
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text)


class ExtractionCandidate(TimestampMixin, Base):
    """Staging table for a future LLM-assisted extractor — reserved now,
    unused by any pipeline in Phase 3B. Nothing writes here yet.

    Design intent: PDF/text -> LLM extraction -> ExtractionCandidate ->
    human review -> verified domain record (OrderBookSnapshot/Guidance/etc).
    LLM output must never bypass this table and directly become a verified
    fact row — review_status starts "pending" and a human must explicitly
    accept/reject/edit before resulting_record_id is set.
    """

    __tablename__ = "extraction_candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True, nullable=False
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_documents.id"), index=True, nullable=False
    )
    candidate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    extracted_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    extraction_method: Mapped[str] = mapped_column(
        String(20), nullable=False, default="LLM_ASSISTED"
    )
    extractor_version: Mapped[str | None] = mapped_column(String(50))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_quote: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )  # pending|accepted|rejected|edited
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    resulting_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
