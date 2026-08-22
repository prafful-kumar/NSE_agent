"""Deterministic valuation-v1 evidence fields and PE input ledger.

Revision ID: 021
Revises: 020
"""

import sqlalchemy as sa
from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, column in (
        ("valuation_model_version", sa.Column("valuation_model_version", sa.String(100))),
        ("earnings_basis", sa.Column("earnings_basis", sa.String(100))),
        ("eps_low", sa.Column("eps_low", sa.Numeric(10, 4))),
        ("eps_mid", sa.Column("eps_mid", sa.Numeric(10, 4))),
        ("eps_high", sa.Column("eps_high", sa.Numeric(10, 4))),
        ("pe_low", sa.Column("pe_low", sa.Numeric(10, 4))),
        ("pe_mid", sa.Column("pe_mid", sa.Numeric(10, 4))),
        ("pe_high", sa.Column("pe_high", sa.Numeric(10, 4))),
        ("upside_downside_low_pct", sa.Column("upside_downside_low_pct", sa.Numeric(10, 4))),
        ("upside_downside_high_pct", sa.Column("upside_downside_high_pct", sa.Numeric(10, 4))),
        ("assumptions", sa.Column("assumptions", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))),
    ):
        op.add_column("valuation_snapshots", column)
    op.create_table(
        "valuation_multiple_inputs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pe_low", sa.Numeric(10, 4), nullable=False),
        sa.Column("pe_mid", sa.Numeric(10, 4), nullable=False),
        sa.Column("pe_high", sa.Numeric(10, 4), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("source_document_id", sa.UUID()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("valuation_multiple_inputs")
    for name in ("assumptions", "upside_downside_high_pct", "upside_downside_low_pct", "pe_high", "pe_mid", "pe_low", "eps_high", "eps_mid", "eps_low", "earnings_basis", "valuation_model_version"):
        op.drop_column("valuation_snapshots", name)
