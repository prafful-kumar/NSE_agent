"""Make earnings horizons explicit and prevent quarterly EPS misuse.

Revision ID: 022
Revises: 021
"""

import sqlalchemy as sa
from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("estimate_runs", sa.Column("earnings_horizon", sa.String(30), nullable=False, server_default="NEXT_QUARTER"))
    op.add_column("valuation_snapshots", sa.Column("earnings_horizon", sa.String(30)))
    op.add_column("valuation_multiple_inputs", sa.Column("earnings_horizon", sa.String(30), nullable=False, server_default="TTM"))


def downgrade() -> None:
    op.drop_column("valuation_multiple_inputs", "earnings_horizon")
    op.drop_column("valuation_snapshots", "earnings_horizon")
    op.drop_column("estimate_runs", "earnings_horizon")
