"""Initial schema: all Phase 1 tables

Revision ID: 001
Revises:
Create Date: 2026-08-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable required PostgreSQL extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")

    # ── companies ─────────────────────────────────────────────────────────────
    op.create_table(
        "companies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("isin", sa.String(20)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("exchange", sa.String(10), nullable=False),
        sa.Column("sector", sa.String(100)),
        sa.Column("industry", sa.String(100)),
        sa.Column("market_cap_category", sa.String(20)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("metadata", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_companies_symbol", "companies", ["symbol"], unique=True)
    op.create_index("ix_companies_isin", "companies", ["isin"], unique=True)

    # ── portfolio_snapshots ───────────────────────────────────────────────────
    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("total_value", sa.Numeric(18, 2)),
        sa.Column("total_invested", sa.Numeric(18, 2)),
        sa.Column("total_pnl", sa.Numeric(18, 2)),
        sa.Column("pnl_pct", sa.Numeric(8, 4)),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True)),
        sa.Column("ingestion_timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("raw_response", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "snapshot_date", "source", name="uq_snapshot_user_date_src"),
    )
    op.create_index("ix_portfolio_snapshots_user_id", "portfolio_snapshots", ["user_id"])
    op.create_index("ix_portfolio_snapshots_snapshot_date", "portfolio_snapshots", ["snapshot_date"])

    # ── holdings ──────────────────────────────────────────────────────────────
    op.create_table(
        "holdings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("snapshot_id", UUID(as_uuid=True), sa.ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id")),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("isin", sa.String(20)),
        sa.Column("quantity", sa.BigInteger, nullable=False),
        sa.Column("t1_quantity", sa.BigInteger),
        sa.Column("average_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("last_price", sa.Numeric(18, 4)),
        sa.Column("current_value", sa.Numeric(18, 2)),
        sa.Column("pnl", sa.Numeric(18, 2)),
        sa.Column("pnl_pct", sa.Numeric(8, 4)),
        sa.Column("portfolio_weight_pct", sa.Numeric(8, 4)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_holdings_snapshot_id", "holdings", ["snapshot_id"])
    op.create_index("ix_holdings_symbol", "holdings", ["symbol"])

    # ── watchlist ─────────────────────────────────────────────────────────────
    op.create_table(
        "watchlist",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id")),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),
    )
    op.create_index("ix_watchlist_user_id", "watchlist", ["user_id"])

    # ── user_preferences ──────────────────────────────────────────────────────
    op.create_table(
        "user_preferences",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("user_id", sa.String(100), nullable=False, unique=True),
        sa.Column("preferred_sectors", JSONB),
        sa.Column("avoided_sectors", JSONB),
        sa.Column("avoided_stocks", JSONB),
        sa.Column("preferred_holding_period_months", sa.Integer),
        sa.Column("risk_tolerance", sa.String(20)),
        sa.Column("max_stock_allocation_pct", sa.Numeric(5, 2)),
        sa.Column("max_sector_allocation_pct", sa.Numeric(5, 2)),
        sa.Column("min_market_cap_category", sa.String(20)),
        sa.Column("valuation_preference", JSONB),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_user_preferences_user_id", "user_preferences", ["user_id"], unique=True)

    # ── investment_theses ─────────────────────────────────────────────────────
    op.create_table(
        "investment_theses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id")),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("thesis", sa.Text),
        sa.Column("buy_reasons", JSONB),
        sa.Column("risk_factors", JSONB),
        sa.Column("catalysts", JSONB),
        sa.Column("invalidation_conditions", JSONB),
        sa.Column("target_price_low", sa.Numeric(18, 2)),
        sa.Column("target_price_base", sa.Numeric(18, 2)),
        sa.Column("target_price_high", sa.Numeric(18, 2)),
        sa.Column("horizon_months", sa.Integer),
        sa.Column("entry_price", sa.Numeric(18, 4)),
        sa.Column("exit_price", sa.Numeric(18, 4)),
        sa.Column("outcome_notes", sa.Text),
        sa.Column("embedding", Vector(1536)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_investment_theses_user_id", "investment_theses", ["user_id"])
    op.create_index("ix_investment_theses_symbol", "investment_theses", ["symbol"])
    op.create_index("ix_investment_theses_status", "investment_theses", ["status"])

    # ── corporate_events ──────────────────────────────────────────────────────
    op.create_table(
        "corporate_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id")),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("announced_date", sa.Date),
        sa.Column("event_date", sa.Date, nullable=False),
        sa.Column("ex_date", sa.Date),
        sa.Column("record_date", sa.Date),
        sa.Column("payment_date", sa.Date),
        sa.Column("amount", sa.Numeric(18, 4)),
        sa.Column("amount_currency", sa.String(10)),
        sa.Column("ratio", sa.String(50)),
        sa.Column("details", JSONB),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("source_timestamp", sa.DateTime(timezone=True)),
        sa.Column("ingestion_timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_confirmed", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("symbol", "event_type", "event_date", "source", name="uq_event_symbol_type_date_src"),
    )
    op.create_index("ix_corporate_events_symbol", "corporate_events", ["symbol"])
    op.create_index("ix_corporate_events_event_type", "corporate_events", ["event_type"])
    op.create_index("ix_corporate_events_event_date", "corporate_events", ["event_date"])

    # ── financial_quarters ────────────────────────────────────────────────────
    op.create_table(
        "financial_quarters",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("fiscal_year", sa.Integer, nullable=False),
        sa.Column("quarter", sa.String(5), nullable=False),
        sa.Column("period_end_date", sa.Date, nullable=False),
        sa.Column("result_date", sa.Date),
        sa.Column("revenue", sa.Numeric(20, 2)),
        sa.Column("ebitda", sa.Numeric(20, 2)),
        sa.Column("ebitda_margin_pct", sa.Numeric(8, 4)),
        sa.Column("pat", sa.Numeric(20, 2)),
        sa.Column("pat_margin_pct", sa.Numeric(8, 4)),
        sa.Column("eps_basic", sa.Numeric(10, 4)),
        sa.Column("eps_diluted", sa.Numeric(10, 4)),
        sa.Column("total_debt", sa.Numeric(20, 2)),
        sa.Column("cash_equivalents", sa.Numeric(20, 2)),
        sa.Column("other_metrics", JSONB),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("source_timestamp", sa.DateTime(timezone=True)),
        sa.Column("ingestion_timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_audited", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("company_id", "fiscal_year", "quarter", name="uq_fq_company_fy_q"),
    )
    op.create_index("ix_financial_quarters_symbol", "financial_quarters", ["symbol"])

    # ── ingestion_runs ────────────────────────────────────────────────────────
    op.create_table(
        "ingestion_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("run_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("symbols", JSONB),
        sa.Column("records_ingested", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text),
        sa.Column("run_metadata", JSONB),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_ingestion_runs_run_type", "ingestion_runs", ["run_type"])

    # ── recommendations ───────────────────────────────────────────────────────
    op.create_table(
        "recommendations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id")),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("horizon", sa.String(50)),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("thesis_status", sa.String(30)),
        sa.Column("fair_value_low", sa.Numeric(18, 2)),
        sa.Column("fair_value_base", sa.Numeric(18, 2)),
        sa.Column("fair_value_high", sa.Numeric(18, 2)),
        sa.Column("reasons", JSONB),
        sa.Column("risks", JSONB),
        sa.Column("invalidation_conditions", JSONB),
        sa.Column("upcoming_events", JSONB),
        sa.Column("evidence", JSONB),
        sa.Column("data_freshness", JSONB),
        sa.Column("requires_human_review", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("prompt_version", sa.String(50)),
        sa.Column("model_version", sa.String(100)),
        sa.Column("raw_llm_output", JSONB),
        sa.Column("human_approved", sa.Boolean),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_recommendations_user_id", "recommendations", ["user_id"])
    op.create_index("ix_recommendations_symbol", "recommendations", ["symbol"])


def downgrade() -> None:
    op.drop_table("recommendations")
    op.drop_table("ingestion_runs")
    op.drop_table("financial_quarters")
    op.drop_table("corporate_events")
    op.drop_table("investment_theses")
    op.drop_table("user_preferences")
    op.drop_table("watchlist")
    op.drop_table("holdings")
    op.drop_table("portfolio_snapshots")
    op.drop_table("companies")
