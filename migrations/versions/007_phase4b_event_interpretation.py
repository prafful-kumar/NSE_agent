"""Phase 4B: event interpretation (event_interpretations) — deterministic
rules + LLM-assisted candidates, always human-reviewed before becoming a
Catalyst/RiskObservation/ThesisChange.

Revision ID: 007
Revises: 006
Create Date: 2026-08-18
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "event_interpretations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("news_event_id", UUID(as_uuid=True), sa.ForeignKey("news_events.id"), nullable=False),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("impact_classification", JSONB, nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("candidate_catalyst", JSONB),
        sa.Column("candidate_risk", JSONB),
        sa.Column("candidate_thesis_change", JSONB),
        sa.Column("extraction_method", sa.String(20), nullable=False, server_default="DETERMINISTIC"),
        sa.Column("extractor_version", sa.String(50)),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(100)),
        sa.Column("resulting_catalyst_id", UUID(as_uuid=True), sa.ForeignKey("catalysts.id")),
        sa.Column("resulting_risk_observation_id", UUID(as_uuid=True), sa.ForeignKey("risk_observations.id")),
        sa.Column("resulting_thesis_change_id", UUID(as_uuid=True), sa.ForeignKey("thesis_changes.id")),
        *_timestamp_columns(),
    )
    op.create_index("ix_event_interpretations_news_event_id", "event_interpretations", ["news_event_id"])
    op.create_index("ix_event_interpretations_company_id", "event_interpretations", ["company_id"])
    op.create_index("ix_event_interpretations_review_status", "event_interpretations", ["review_status"])


def downgrade() -> None:
    op.drop_table("event_interpretations")
