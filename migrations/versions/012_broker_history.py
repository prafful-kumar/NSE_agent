"""Phase 6A: broker-account historical import.

Adds broker_accounts, historical_import_runs, historical_trades,
historical_cash_flows, historical_dividends, historical_position_lots.

Provider-neutral schema: one BrokerAccount per (user, broker,
account_label). Fact tables (trades/cash_flows/dividends) are deduplicated
on (broker_account_id, dedupe_key) rather than file identity, because
statement exports (e.g. Zerodha Console tradebook, capped at 365 days per
download) routinely overlap in date range and must merge idempotently.
historical_position_lots is the output of Phase 6B lot reconstruction, not
written by importers -- created now so the table exists when that phase
lands without a further migration.

Revision ID: 012
Revises: 011
Create Date: 2026-08-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "broker_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("broker", sa.String(20), nullable=False),
        sa.Column("account_label", sa.String(100), nullable=False),
        sa.Column("strategy_profile", sa.String(20), nullable=False),
        sa.UniqueConstraint("user_id", "broker", "account_label", name="uq_broker_account_user_broker_label"),
    )
    op.create_index("ix_broker_accounts_user_id", "broker_accounts", ["user_id"])

    op.create_table(
        "historical_import_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "broker_account_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("trades_created", sa.Integer, nullable=False, server_default="0"),
        sa.Column("trades_skipped_duplicate", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cash_flows_created", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cash_flows_skipped_duplicate", sa.Integer, nullable=False, server_default="0"),
        sa.Column("dividends_created", sa.Integer, nullable=False, server_default="0"),
        sa.Column("dividends_skipped_duplicate", sa.Integer, nullable=False, server_default="0"),
        sa.Column("date_range_start", sa.Date),
        sa.Column("date_range_end", sa.Date),
        sa.Column("warnings", postgresql.JSONB),
        sa.Column("error_message", sa.Text),
        sa.UniqueConstraint("broker_account_id", "file_hash", name="uq_import_run_account_filehash"),
    )
    op.create_index("ix_historical_import_runs_broker_account_id", "historical_import_runs", ["broker_account_id"])

    op.create_table(
        "historical_trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "broker_account_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("isin", sa.String(20)),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id")),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("trade_datetime", sa.DateTime(timezone=True)),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("price", sa.Numeric(18, 4), nullable=False),
        sa.Column("exchange", sa.String(10)),
        sa.Column("segment", sa.String(20)),
        sa.Column("product", sa.String(10)),
        sa.Column("order_id", sa.String(50)),
        sa.Column("charges", sa.Numeric(18, 4)),
        sa.Column(
            "source_import_run_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("historical_import_runs.id"),
        ),
        sa.Column("raw", postgresql.JSONB),
        sa.UniqueConstraint("broker_account_id", "dedupe_key", name="uq_historical_trade_account_dedupe"),
    )
    op.create_index("ix_historical_trades_broker_account_id", "historical_trades", ["broker_account_id"])
    op.create_index("ix_historical_trades_symbol", "historical_trades", ["symbol"])
    op.create_index("ix_historical_trades_company_id", "historical_trades", ["company_id"])
    op.create_index("ix_historical_trades_trade_date", "historical_trades", ["trade_date"])

    op.create_table(
        "historical_cash_flows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "broker_account_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("flow_date", sa.Date, nullable=False),
        sa.Column("flow_type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column(
            "source_import_run_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("historical_import_runs.id"),
        ),
        sa.Column("raw", postgresql.JSONB),
        sa.UniqueConstraint("broker_account_id", "dedupe_key", name="uq_historical_cashflow_account_dedupe"),
    )
    op.create_index("ix_historical_cash_flows_broker_account_id", "historical_cash_flows", ["broker_account_id"])
    op.create_index("ix_historical_cash_flows_flow_date", "historical_cash_flows", ["flow_date"])

    op.create_table(
        "historical_dividends",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "broker_account_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("isin", sa.String(20)),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id")),
        sa.Column("payment_date", sa.Date, nullable=False),
        sa.Column("quantity_held", sa.Numeric(18, 4)),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column(
            "source_import_run_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("historical_import_runs.id"),
        ),
        sa.Column("raw", postgresql.JSONB),
        sa.UniqueConstraint("broker_account_id", "dedupe_key", name="uq_historical_dividend_account_dedupe"),
    )
    op.create_index("ix_historical_dividends_broker_account_id", "historical_dividends", ["broker_account_id"])
    op.create_index("ix_historical_dividends_symbol", "historical_dividends", ["symbol"])
    op.create_index("ix_historical_dividends_company_id", "historical_dividends", ["company_id"])
    op.create_index("ix_historical_dividends_payment_date", "historical_dividends", ["payment_date"])

    op.create_table(
        "historical_position_lots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "broker_account_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id")),
        sa.Column("opened_date", sa.Date, nullable=False),
        sa.Column("quantity_opened", sa.Numeric(18, 4), nullable=False),
        sa.Column("quantity_remaining", sa.Numeric(18, 4), nullable=False),
        sa.Column("cost_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("closed_date", sa.Date),
        sa.Column("realized_pnl", sa.Numeric(18, 4)),
        sa.Column("opening_trade_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("historical_trades.id")),
    )
    op.create_index("ix_historical_position_lots_broker_account_id", "historical_position_lots", ["broker_account_id"])
    op.create_index("ix_historical_position_lots_symbol", "historical_position_lots", ["symbol"])
    op.create_index("ix_historical_position_lots_company_id", "historical_position_lots", ["company_id"])
    op.create_index("ix_historical_position_lots_closed_date", "historical_position_lots", ["closed_date"])


def downgrade() -> None:
    op.drop_table("historical_position_lots")
    op.drop_table("historical_dividends")
    op.drop_table("historical_cash_flows")
    op.drop_table("historical_trades")
    op.drop_table("historical_import_runs")
    op.drop_table("broker_accounts")
