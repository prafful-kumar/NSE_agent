"""Phase 6G: retain NIFTY 50 TRI outcomes alongside the price index.

The existing benchmark/excess columns remain the price-index series.  TRI
columns are additive, allowing every historic report to disclose which
benchmark was used instead of silently changing prior conclusions.

Revision ID: 017
Revises: 016
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for label in ("1m", "3m", "6m", "12m"):
        op.add_column("walk_forward_outcomes", sa.Column(f"benchmark_tri_return_{label}", sa.Numeric(10, 6)))
        op.add_column("walk_forward_outcomes", sa.Column(f"excess_return_tri_{label}", sa.Numeric(10, 6)))


def downgrade() -> None:
    for label in ("12m", "6m", "3m", "1m"):
        op.drop_column("walk_forward_outcomes", f"excess_return_tri_{label}")
        op.drop_column("walk_forward_outcomes", f"benchmark_tri_return_{label}")
