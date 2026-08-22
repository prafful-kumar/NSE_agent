"""Phase 7E: versioned, immutable active-thesis artifacts.

Revision ID: 023
Revises: 022
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("investment_theses", sa.Column("thesis_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("investment_theses", sa.Column("thesis_model_version", sa.String(50), nullable=False, server_default="active-thesis-v1"))
    op.add_column("investment_theses", sa.Column("as_of", sa.DateTime(timezone=True)))
    op.add_column("investment_theses", sa.Column("evidence_refs", JSONB))
    op.add_column("investment_theses", sa.Column("supersedes_thesis_id", UUID(as_uuid=True), sa.ForeignKey("investment_theses.id")))
    op.create_index("ix_investment_theses_as_of", "investment_theses", ["as_of"])
    op.create_index("ix_investment_theses_supersedes_thesis_id", "investment_theses", ["supersedes_thesis_id"])
    op.execute("""
        CREATE FUNCTION prevent_investment_thesis_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'investment_theses are immutable; append a successor version instead';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER investment_theses_immutable
        BEFORE UPDATE OR DELETE ON investment_theses
        FOR EACH ROW EXECUTE FUNCTION prevent_investment_thesis_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER investment_theses_immutable ON investment_theses")
    op.execute("DROP FUNCTION prevent_investment_thesis_mutation()")
    op.drop_index("ix_investment_theses_supersedes_thesis_id", table_name="investment_theses")
    op.drop_index("ix_investment_theses_as_of", table_name="investment_theses")
    op.drop_column("investment_theses", "supersedes_thesis_id")
    op.drop_column("investment_theses", "evidence_refs")
    op.drop_column("investment_theses", "as_of")
    op.drop_column("investment_theses", "thesis_model_version")
    op.drop_column("investment_theses", "thesis_version")
