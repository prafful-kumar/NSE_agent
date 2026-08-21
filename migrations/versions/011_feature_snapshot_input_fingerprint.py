"""feature_snapshots input-aware cache invalidation: adds input_fingerprint
and feature_builder_version, and widens the uniqueness key to include them.

Fixes a real staleness bug found on the BEL backtest: a FeatureSnapshot was
previously content-addressed only by (company_id, financial_period_id,
cutoff_at), so once cached it was reused forever even after newly-transcribed
historical FinancialResult rows became visible at that same cutoff_at. Every
pre-existing snapshot gets input_fingerprint='' via the server default, which
never matches a freshly-computed (non-empty) fingerprint -- so the next
build_feature_snapshot call for any of them naturally rebuilds from current
data instead of requiring a manual cache clear.

Revision ID: 011
Revises: 010
Create Date: 2026-08-18
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "feature_snapshots",
        sa.Column("input_fingerprint", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "feature_snapshots",
        sa.Column("feature_builder_version", sa.String(20), nullable=False, server_default=""),
    )
    op.drop_constraint(
        "uq_feature_snapshot_company_period_cutoff", "feature_snapshots", type_="unique"
    )
    op.create_unique_constraint(
        "uq_feature_snapshot_company_period_cutoff_fingerprint",
        "feature_snapshots",
        ["company_id", "financial_period_id", "cutoff_at", "input_fingerprint", "feature_builder_version"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_feature_snapshot_company_period_cutoff_fingerprint", "feature_snapshots", type_="unique"
    )
    op.create_unique_constraint(
        "uq_feature_snapshot_company_period_cutoff",
        "feature_snapshots",
        ["company_id", "financial_period_id", "cutoff_at"],
    )
    op.drop_column("feature_snapshots", "feature_builder_version")
    op.drop_column("feature_snapshots", "input_fingerprint")
