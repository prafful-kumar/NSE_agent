"""Phase 3B Part B: filing discovery — parent_document_id on source_documents
(links a ZIP-extracted child document back to the archived parent ZIP).

Revision ID: 005
Revises: 004
Create Date: 2026-08-18
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "source_documents",
        sa.Column(
            "parent_document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("source_documents.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_source_documents_parent_document_id",
        "source_documents",
        ["parent_document_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_documents_parent_document_id", table_name="source_documents")
    op.drop_column("source_documents", "parent_document_id")
