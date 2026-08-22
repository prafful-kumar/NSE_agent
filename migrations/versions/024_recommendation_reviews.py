"""Phase 7H: immutable recommendations, evidence snapshots, and reviews.

Revision ID: 024
Revises: 023
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recommendation_reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "recommendation_decision_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recommendation_decisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("verdict", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("verdict IN ('AGREE', 'DISAGREE', 'UNSURE')", name="ck_recommendation_review_verdict"),
    )
    op.create_index("ix_recommendation_reviews_recommendation_decision_id", "recommendation_reviews", ["recommendation_decision_id"])
    op.execute("""
        CREATE FUNCTION prevent_recommendation_artifact_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'recommendation artifacts are immutable observations';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER recommendation_runs_immutable
        BEFORE UPDATE OR DELETE ON recommendation_runs
        FOR EACH ROW EXECUTE FUNCTION prevent_recommendation_artifact_mutation();
    """)
    op.execute("""
        CREATE TRIGGER recommendation_decisions_immutable
        BEFORE UPDATE OR DELETE ON recommendation_decisions
        FOR EACH ROW EXECUTE FUNCTION prevent_recommendation_artifact_mutation();
    """)
    op.execute("""
        CREATE TRIGGER recommendation_evidence_links_immutable
        BEFORE UPDATE OR DELETE ON recommendation_evidence_links
        FOR EACH ROW EXECUTE FUNCTION prevent_recommendation_artifact_mutation();
    """)
    op.execute("""
        CREATE TRIGGER recommendation_reviews_immutable
        BEFORE UPDATE OR DELETE ON recommendation_reviews
        FOR EACH ROW EXECUTE FUNCTION prevent_recommendation_artifact_mutation();
    """)


def downgrade() -> None:
    # Some local/test databases received the earlier review-only draft of this
    # migration, before the three recommendation-artifact triggers existed.
    # Keep rollback safe for that state as well.
    op.execute("DROP TRIGGER IF EXISTS recommendation_reviews_immutable ON recommendation_reviews")
    op.execute(
        "DROP TRIGGER IF EXISTS recommendation_evidence_links_immutable ON recommendation_evidence_links"
    )
    op.execute("DROP TRIGGER IF EXISTS recommendation_decisions_immutable ON recommendation_decisions")
    op.execute("DROP TRIGGER IF EXISTS recommendation_runs_immutable ON recommendation_runs")
    op.execute("DROP FUNCTION IF EXISTS prevent_recommendation_artifact_mutation()")
    op.drop_index("ix_recommendation_reviews_recommendation_decision_id", table_name="recommendation_reviews")
    op.drop_table("recommendation_reviews")
