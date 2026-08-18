"""Phase 5B: point-in-time backtesting (backtest_scores) — one immutable
score row per EstimateRun, scoring it against the actual FinancialResult
once known.

Revision ID: 009
Revises: 008
Create Date: 2026-08-18
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "backtest_scores",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("financial_period_id", UUID(as_uuid=True), sa.ForeignKey("financial_periods.id"), nullable=False),
        sa.Column("estimate_run_id", UUID(as_uuid=True), sa.ForeignKey("estimate_runs.id"), nullable=False, unique=True),
        sa.Column("financial_result_id", UUID(as_uuid=True), sa.ForeignKey("financial_results.id"), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("revenue_error_pct", sa.Numeric(8, 4)),
        sa.Column("pat_error_pct", sa.Numeric(8, 4)),
        sa.Column("eps_error_pct", sa.Numeric(8, 4)),
        sa.Column("margin_error_bps", sa.Numeric(10, 2)),
        sa.Column("within_band_revenue", sa.Boolean()),
        sa.Column("within_band_pat", sa.Boolean()),
        sa.Column("within_band_eps", sa.Boolean()),
        sa.Column("surprise_direction", sa.String(10)),
        sa.Column("growth_direction_correct", sa.Boolean()),
        sa.Column("confidence_bucket", sa.String(10)),
        *_timestamp_columns(),
    )
    op.create_index("ix_backtest_scores_company_id", "backtest_scores", ["company_id"])
    op.create_index("ix_backtest_scores_financial_period_id", "backtest_scores", ["financial_period_id"])
    op.create_index("ix_backtest_scores_financial_result_id", "backtest_scores", ["financial_result_id"])
    op.create_index("ix_backtest_scores_cutoff_at", "backtest_scores", ["cutoff_at"])
    op.create_index("ix_backtest_scores_model_version", "backtest_scores", ["model_version"])


def downgrade() -> None:
    op.drop_table("backtest_scores")
