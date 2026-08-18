"""Phase 5B PIT/verification fixes: financial_results.timestamp_precision
(EXACT|DATE_ONLY, so a date-only historical result never masquerades as an
exact intraday timestamp) and backtest_scores diagnostic columns
(status/unscorable_reason/verified_history_count/unverified_history_count,
so a period with no verified history produces an explicit UNSCORABLE row
instead of a misleading all-null "successful" score).

Revision ID: 010
Revises: 009
Create Date: 2026-08-18
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "financial_results",
        sa.Column(
            "timestamp_precision", sa.String(10), nullable=False, server_default="EXACT"
        ),
    )
    op.add_column(
        "backtest_scores",
        sa.Column("status", sa.String(12), nullable=False, server_default="SCORED"),
    )
    op.add_column("backtest_scores", sa.Column("unscorable_reason", sa.String(40)))
    op.add_column(
        "backtest_scores",
        sa.Column(
            "verified_history_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "backtest_scores",
        sa.Column(
            "unverified_history_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    op.drop_column("backtest_scores", "unverified_history_count")
    op.drop_column("backtest_scores", "verified_history_count")
    op.drop_column("backtest_scores", "unscorable_reason")
    op.drop_column("backtest_scores", "status")
    op.drop_column("financial_results", "timestamp_precision")
