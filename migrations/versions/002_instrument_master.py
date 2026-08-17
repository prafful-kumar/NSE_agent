"""Add instrument_master table.

Revision ID: 002
Revises: 001
Create Date: 2026-08-17
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instrument_master",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tradingsymbol", sa.String(50), nullable=False),
        sa.Column("exchange", sa.String(10), nullable=False),
        sa.Column("isin", sa.String(20), nullable=True),
        sa.Column("instrument_type", sa.String(20), nullable=False, server_default="EQ"),
        sa.Column("zerodha_instrument_token", sa.BigInteger, nullable=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tradingsymbol", "exchange", name="uq_instrument_symbol_exchange"),
    )
    op.create_index("ix_instrument_master_tradingsymbol", "instrument_master", ["tradingsymbol"])
    op.create_index("ix_instrument_master_isin", "instrument_master", ["isin"])
    op.create_index(
        "ix_instrument_master_token",
        "instrument_master",
        ["zerodha_instrument_token"],
        postgresql_where=sa.text("zerodha_instrument_token IS NOT NULL"),
    )
    op.create_index("ix_instrument_master_company_id", "instrument_master", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_instrument_master_company_id", "instrument_master")
    op.drop_index("ix_instrument_master_token", "instrument_master")
    op.drop_index("ix_instrument_master_isin", "instrument_master")
    op.drop_index("ix_instrument_master_tradingsymbol", "instrument_master")
    op.drop_table("instrument_master")
