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

    # Relationships
    holdings: Mapped[list["Holding"]] = relationship(back_populates="company")
    watchlist_items: Mapped[list["WatchlistItem"]] = relationship(back_populates="company")
    investment_theses: Mapped[list["InvestmentThesis"]] = relationship(
        back_populates="company"
    )
    corporate_events: Mapped[list["CorporateEvent"]] = relationship(back_populates="company")
    financial_quarters: Mapped[list["FinancialQuarter"]] = relationship(
        back_populates="company"
    )

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


# ── Corporate actions / Events calendar ──────────────────────────────────────

class CorporateEvent(TimestampMixin, SourceMixin, Base):
    """Corporate actions sourced from NSE/BSE/company filings.

    Primary source required for dividend dates; never infer payment dates.
    """

    __tablename__ = "corporate_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # Tier-1 event types: dividend|ex_dividend|record_date|payment_date|
    # result_date|agm|bonus|split|buyback|rights|other
    event_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    announced_date: Mapped[date | None] = mapped_column(Date)
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    ex_date: Mapped[date | None] = mapped_column(Date, index=True)
    record_date: Mapped[date | None] = mapped_column(Date)
    # Payment date is NOT inferred; only stored when published
    payment_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    amount_currency: Mapped[str | None] = mapped_column(String(10))
    ratio: Mapped[str | None] = mapped_column(String(50))  # e.g. "1:2" for bonus
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_url: Mapped[str | None] = mapped_column(Text)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    company: Mapped["Company | None"] = relationship(back_populates="corporate_events")

    __table_args__ = (
        UniqueConstraint(
            "symbol", "event_type", "event_date", "source",
            name="uq_event_symbol_type_date_src",
        ),
    )


# ── Financial quarters ────────────────────────────────────────────────────────

class FinancialQuarter(TimestampMixin, SourceMixin, Base):
    """Actual reported quarterly financial results."""

    __tablename__ = "financial_quarters"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)  # e.g. 2025
    quarter: Mapped[str] = mapped_column(String(5), nullable=False)  # Q1|Q2|Q3|Q4
    period_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    result_date: Mapped[date | None] = mapped_column(Date)

    # Revenue / Topline
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    # EBITDA
    ebitda: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    ebitda_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    # PAT
    pat: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    pat_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    # EPS
    eps_basic: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    eps_diluted: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    # Balance sheet items
    total_debt: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    cash_equivalents: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    # Other
    other_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_url: Mapped[str | None] = mapped_column(Text)
    is_audited: Mapped[bool] = mapped_column(Boolean, default=False)

    company: Mapped["Company"] = relationship(back_populates="financial_quarters")

    __table_args__ = (
        UniqueConstraint("company_id", "fiscal_year", "quarter", name="uq_fq_company_fy_q"),
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
