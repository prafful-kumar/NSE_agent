"""Phase 6C.0: historical market price foundation.

Adds three tables:
- price_archive_files: Tier-1 archive for a downloaded, market-wide NSE
  bhavcopy / index close-all file (one row per trading_date + data_type).
  Deliberately NOT company-scoped, unlike source_documents -- see the
  PriceArchiveFile docstring in db/models.py for why.
- daily_prices: one equity's raw (never adjusted) daily OHLCV bar.
- benchmark_prices: one benchmark index's raw daily OHLC bar (starting
  with NIFTY 50 price index; NIFTY 50 TRI can be added later as a new
  benchmark_code value without a schema change).

Revision ID: 014
Revises: 013
Create Date: 2026-08-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "price_archive_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("data_type", sa.String(20), nullable=False),
        sa.Column("source_format", sa.String(30), nullable=False),
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.Text, nullable=False),
        sa.Column("mime_type", sa.String(100)),
        sa.Column("size_bytes", sa.BigInteger),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "trading_date", "data_type", "content_hash",
            name="uq_price_archive_file_date_type_hash",
        ),
    )
    op.create_index("ix_price_archive_files_trading_date", "price_archive_files", ["trading_date"])
    op.create_index("ix_price_archive_files_content_hash", "price_archive_files", ["content_hash"])

    op.create_table(
        "daily_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "company_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id"), nullable=False,
        ),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("open", sa.Numeric(14, 4), nullable=False),
        sa.Column("high", sa.Numeric(14, 4), nullable=False),
        sa.Column("low", sa.Numeric(14, 4), nullable=False),
        sa.Column("close", sa.Numeric(14, 4), nullable=False),
        sa.Column("volume", sa.BigInteger),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column(
            "source_document_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("price_archive_files.id"),
        ),
        sa.Column("adjustment_status", sa.String(20), nullable=False, server_default="RAW"),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("symbol", "trading_date", name="uq_daily_price_symbol_date"),
    )
    op.create_index("ix_daily_prices_company_id", "daily_prices", ["company_id"])
    op.create_index("ix_daily_prices_symbol", "daily_prices", ["symbol"])
    op.create_index("ix_daily_prices_trading_date", "daily_prices", ["trading_date"])
    op.create_index("ix_daily_prices_source_document_id", "daily_prices", ["source_document_id"])

    op.create_table(
        "benchmark_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("benchmark_code", sa.String(50), nullable=False),
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("open", sa.Numeric(14, 4), nullable=False),
        sa.Column("high", sa.Numeric(14, 4), nullable=False),
        sa.Column("low", sa.Numeric(14, 4), nullable=False),
        sa.Column("close", sa.Numeric(14, 4), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column(
            "source_document_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("price_archive_files.id"),
        ),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("benchmark_code", "trading_date", name="uq_benchmark_price_code_date"),
    )
    op.create_index("ix_benchmark_prices_benchmark_code", "benchmark_prices", ["benchmark_code"])
    op.create_index("ix_benchmark_prices_trading_date", "benchmark_prices", ["trading_date"])
    op.create_index(
        "ix_benchmark_prices_source_document_id", "benchmark_prices", ["source_document_id"],
    )


def downgrade() -> None:
    op.drop_table("benchmark_prices")
    op.drop_table("daily_prices")
    op.drop_table("price_archive_files")
