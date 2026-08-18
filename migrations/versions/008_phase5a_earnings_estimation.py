"""Phase 5A: earnings estimation (feature_snapshots, estimate_runs) —
deterministic core, optional LLM narrative assumptions only (no LLM-derived
numbers, enforced at the schema level).

Revision ID: 008
Revises: 007
Create Date: 2026-08-18
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "feature_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("financial_period_id", UUID(as_uuid=True), sa.ForeignKey("financial_periods.id"), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        *_timestamp_columns(),
        sa.UniqueConstraint(
            "company_id", "financial_period_id", "cutoff_at",
            name="uq_feature_snapshot_company_period_cutoff",
        ),
    )
    op.create_index("ix_feature_snapshots_company_id", "feature_snapshots", ["company_id"])
    op.create_index("ix_feature_snapshots_financial_period_id", "feature_snapshots", ["financial_period_id"])
    op.create_index("ix_feature_snapshots_cutoff_at", "feature_snapshots", ["cutoff_at"])

    op.create_table(
        "estimate_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("financial_period_id", UUID(as_uuid=True), sa.ForeignKey("financial_periods.id"), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("feature_snapshot_id", UUID(as_uuid=True), sa.ForeignKey("feature_snapshots.id"), nullable=False),
        sa.Column("revenue_low", sa.Numeric(20, 2)),
        sa.Column("revenue_base", sa.Numeric(20, 2)),
        sa.Column("revenue_high", sa.Numeric(20, 2)),
        sa.Column("ebitda_margin_low", sa.Numeric(8, 4)),
        sa.Column("ebitda_margin_base", sa.Numeric(8, 4)),
        sa.Column("ebitda_margin_high", sa.Numeric(8, 4)),
        sa.Column("pat_low", sa.Numeric(20, 2)),
        sa.Column("pat_base", sa.Numeric(20, 2)),
        sa.Column("pat_high", sa.Numeric(20, 2)),
        sa.Column("eps_low", sa.Numeric(10, 4)),
        sa.Column("eps_base", sa.Numeric(10, 4)),
        sa.Column("eps_high", sa.Numeric(10, 4)),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("assumptions", JSONB, nullable=False),
        *_timestamp_columns(),
    )
    op.create_index("ix_estimate_runs_company_id", "estimate_runs", ["company_id"])
    op.create_index("ix_estimate_runs_financial_period_id", "estimate_runs", ["financial_period_id"])
    op.create_index("ix_estimate_runs_cutoff_at", "estimate_runs", ["cutoff_at"])
    op.create_index("ix_estimate_runs_model_version", "estimate_runs", ["model_version"])
    op.create_index("ix_estimate_runs_feature_snapshot_id", "estimate_runs", ["feature_snapshot_id"])


def downgrade() -> None:
    op.drop_table("estimate_runs")
    op.drop_table("feature_snapshots")
