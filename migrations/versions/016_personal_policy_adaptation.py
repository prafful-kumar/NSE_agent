"""Phase 6F: governed personal policy adaptation.

Candidate rules and review proposals are persisted for audit only.  No
migration changes the live agent policy or creates a rule-to-agent relation.

Revision ID: 016
Revises: 015
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "candidate_policy_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("broker_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("broker_accounts.id"), nullable=False),
        sa.Column("rule_id", sa.String(100), nullable=False, unique=True),
        sa.Column("strategy_profile", sa.String(20), nullable=False),
        sa.Column("feature_condition", postgresql.JSONB, nullable=False),
        sa.Column("affected_action", sa.String(20), nullable=False),
        sa.Column("proposed_adjustment", postgresql.JSONB, nullable=False),
        sa.Column("evidence_window", postgresql.JSONB, nullable=False),
        sa.Column("sample_size", sa.Integer, nullable=False),
        sa.Column("supporting_metrics", postgresql.JSONB, nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_candidate_policy_rules_broker_account_id", "candidate_policy_rules", ["broker_account_id"])
    op.create_index("ix_candidate_policy_rules_rule_id", "candidate_policy_rules", ["rule_id"])
    op.create_index("ix_candidate_policy_rules_status", "candidate_policy_rules", ["status"])
    op.create_table(
        "policy_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("candidate_rule_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("candidate_policy_rules.id"), nullable=False, unique=True),
        sa.Column("current_behavior", sa.Text, nullable=False),
        sa.Column("historical_evidence", sa.Text, nullable=False),
        sa.Column("proposed_adjustment", sa.Text, nullable=False),
        sa.Column("expected_benefit", sa.Text, nullable=False),
        sa.Column("known_risks", sa.Text, nullable=False),
        sa.Column("out_of_sample_result", postgresql.JSONB, nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("policy_proposals")
    op.drop_table("candidate_policy_rules")
