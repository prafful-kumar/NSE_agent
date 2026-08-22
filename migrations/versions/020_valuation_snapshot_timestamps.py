"""Align valuation snapshot persistence with the ORM model.

Revision ID: 020
Revises: 019

Phase 7A's ``ValuationSnapshot`` inherits ``TimestampMixin``.  Revision 019
created the table without those two columns, which made even a read of the
table fail on an upgraded database.  This migration is strictly a persistence
repair: it changes neither valuation nor recommendation rules.
"""

import sqlalchemy as sa
from alembic import op


revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "valuation_snapshots",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.add_column(
        "valuation_snapshots",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("valuation_snapshots", "updated_at")
    op.drop_column("valuation_snapshots", "created_at")
