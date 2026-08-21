"""Phase 7A current recommendation artifacts

Revision ID: 019
Revises: 018
"""
import sqlalchemy as sa
from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("valuation_snapshots", sa.Column("id", sa.UUID(), primary_key=True), sa.Column("company_id", sa.UUID(), nullable=False), sa.Column("as_of", sa.DateTime(timezone=True), nullable=False), sa.Column("valuation_method", sa.String(50), nullable=False), sa.Column("fair_value_low", sa.Numeric(18,2)), sa.Column("fair_value_mid", sa.Numeric(18,2)), sa.Column("fair_value_high", sa.Numeric(18,2)), sa.Column("current_price", sa.Numeric(18,4)), sa.Column("upside_downside_pct", sa.Numeric(10,4)), sa.Column("confidence", sa.Numeric(5,4)), sa.Column("source_document_id", sa.UUID()), sa.Column("evidence", sa.JSON(), nullable=False))
    op.create_table("recommendation_runs", sa.Column("id", sa.UUID(), primary_key=True), sa.Column("user_id", sa.String(100), nullable=False), sa.Column("rule_version", sa.String(100), nullable=False), sa.Column("as_of", sa.DateTime(timezone=True), nullable=False), sa.Column("policy_layer_status", sa.String(30), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_table("recommendation_decisions", sa.Column("id", sa.UUID(), primary_key=True), sa.Column("run_id", sa.UUID(), nullable=False), sa.Column("company_id", sa.UUID()), sa.Column("symbol", sa.String(30), nullable=False), sa.Column("base_action", sa.String(30), nullable=False), sa.Column("final_action", sa.String(30), nullable=False), sa.Column("confidence", sa.Numeric(5,4)), sa.Column("data_quality_status", sa.String(30), nullable=False), sa.Column("data_gaps", sa.JSON(), nullable=False), sa.Column("evidence_summary", sa.JSON(), nullable=False), sa.Column("valuation_inputs", sa.JSON()), sa.Column("risk_inputs", sa.JSON()), sa.Column("portfolio_context", sa.JSON()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.UniqueConstraint("run_id","symbol", name="uq_recommendation_decision_run_symbol"))
    op.create_table("recommendation_evidence_links", sa.Column("id", sa.UUID(), primary_key=True), sa.Column("recommendation_decision_id", sa.UUID(), nullable=False), sa.Column("evidence_type", sa.String(50), nullable=False), sa.Column("evidence_id", sa.UUID()), sa.Column("details", sa.JSON()))

def downgrade() -> None:
    op.drop_table("recommendation_evidence_links"); op.drop_table("recommendation_decisions"); op.drop_table("recommendation_runs"); op.drop_table("valuation_snapshots")
