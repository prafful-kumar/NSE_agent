"""Phase 6B: opening-position adjustments.

Adds opening_position_adjustments: account-scoped, reconciliation-derived
synthetic opening lots for symbols whose real acquisition trade is
missing from the tradebook (e.g. ZAGGLE's SELL-with-no-prior-BUY case).

Stock splits/bonuses do NOT get a new table here -- the existing
corporate_actions table (action_type IN ("split","bonus"), Phase 3B)
already models exactly this fact with full provenance/versioning; Phase
6B's reconstruction engine reads from it directly (see
services/reconstruction/corporate_actions.py) rather than duplicating it.

Revision ID: 013
Revises: 012
Create Date: 2026-08-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "opening_position_adjustments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "broker_account_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("isin", sa.String(20)),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id")),
        sa.Column("opening_date", sa.Date, nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("cost_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("confidence", sa.String(10), nullable=False),
        sa.Column("reason", sa.String(50), nullable=False),
        sa.Column("notes", sa.Text),
        sa.UniqueConstraint(
            "broker_account_id", "symbol", name="uq_opening_position_adjustment_account_symbol",
        ),
    )
    op.create_index(
        "ix_opening_position_adjustments_broker_account_id",
        "opening_position_adjustments", ["broker_account_id"],
    )
    op.create_index("ix_opening_position_adjustments_symbol", "opening_position_adjustments", ["symbol"])
    op.create_index(
        "ix_opening_position_adjustments_company_id", "opening_position_adjustments", ["company_id"],
    )


def downgrade() -> None:
    op.drop_table("opening_position_adjustments")
