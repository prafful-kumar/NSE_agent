"""allow offline comparator insufficient-evidence action

Revision ID: 018
Revises: 017
"""

import sqlalchemy as sa

from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("walk_forward_decisions", "action", type_=sa.String(length=30))


def downgrade() -> None:
    op.alter_column("walk_forward_decisions", "action", type_=sa.String(length=10))
