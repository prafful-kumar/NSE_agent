"""Phase 4A: news ingestion + relational research memory (company_aliases,
news_items, news_events, news_event_items, news_company_links,
research_notes, thesis_changes, catalysts, risk_observations,
source_reliability).

Revision ID: 006
Revises: 005
Create Date: 2026-08-18
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    # ── company_aliases ───────────────────────────────────────────────────────
    op.create_table(
        "company_aliases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.Column("alias_type", sa.String(20), nullable=False),
        sa.Column("match_confidence", sa.Numeric(3, 2), nullable=False, server_default="1.00"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        *_timestamp_columns(),
        sa.UniqueConstraint("company_id", "alias", name="uq_company_alias_company_alias"),
    )
    op.create_index("ix_company_aliases_company_id", "company_aliases", ["company_id"])
    op.create_index("ix_company_aliases_alias", "company_aliases", ["alias"])

    # ── news_items ────────────────────────────────────────────────────────────
    op.create_table(
        "news_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("headline", sa.String(1000), nullable=False),
        sa.Column("feed_description", sa.Text),
        sa.Column("publisher", sa.String(255)),
        sa.Column("source_name", sa.String(50), nullable=False),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("canonical_url", sa.Text),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("raw_metadata", JSONB),
        *_timestamp_columns(),
        sa.UniqueConstraint("source_name", "source_url", name="uq_news_item_source_url"),
    )
    op.create_index("ix_news_items_source_name", "news_items", ["source_name"])
    op.create_index("ix_news_items_published_at", "news_items", ["published_at"])
    op.create_index("ix_news_items_discovered_at", "news_items", ["discovered_at"])
    op.create_index("ix_news_items_content_hash", "news_items", ["content_hash"])

    # ── news_events ───────────────────────────────────────────────────────────
    op.create_table(
        "news_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(50), nullable=False, server_default="unclassified"),
        sa.Column("primary_company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id")),
        sa.Column("event_date", sa.Date),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("importance", sa.String(20), nullable=False, server_default="unclassified"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("representative_headline", sa.String(1000), nullable=False),
        *_timestamp_columns(),
    )
    op.create_index("ix_news_events_event_type", "news_events", ["event_type"])
    op.create_index("ix_news_events_primary_company_id", "news_events", ["primary_company_id"])
    op.create_index("ix_news_events_event_date", "news_events", ["event_date"])

    # ── news_event_items ──────────────────────────────────────────────────────
    op.create_table(
        "news_event_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("news_event_id", UUID(as_uuid=True), sa.ForeignKey("news_events.id"), nullable=False),
        sa.Column("news_item_id", UUID(as_uuid=True), sa.ForeignKey("news_items.id"), nullable=False),
        *_timestamp_columns(),
        sa.UniqueConstraint("news_event_id", "news_item_id", name="uq_news_event_item"),
    )
    op.create_index("ix_news_event_items_news_event_id", "news_event_items", ["news_event_id"])
    op.create_index("ix_news_event_items_news_item_id", "news_event_items", ["news_item_id"])

    # ── news_company_links ────────────────────────────────────────────────────
    op.create_table(
        "news_company_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("news_item_id", UUID(as_uuid=True), sa.ForeignKey("news_items.id"), nullable=False),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("relevance_score", sa.Numeric(3, 2), nullable=False),
        sa.Column("match_method", sa.String(30), nullable=False),
        *_timestamp_columns(),
        sa.UniqueConstraint("news_item_id", "company_id", name="uq_news_company_link"),
    )
    op.create_index("ix_news_company_links_news_item_id", "news_company_links", ["news_item_id"])
    op.create_index("ix_news_company_links_company_id", "news_company_links", ["company_id"])

    # ── research_notes ────────────────────────────────────────────────────────
    op.create_table(
        "research_notes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("note_type", sa.String(30), nullable=False, server_default="manual"),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_document_id", UUID(as_uuid=True), sa.ForeignKey("source_documents.id")),
        sa.Column("news_event_id", UUID(as_uuid=True), sa.ForeignKey("news_events.id")),
        sa.Column("created_by", sa.String(100), nullable=False),
        *_timestamp_columns(),
    )
    op.create_index("ix_research_notes_company_id", "research_notes", ["company_id"])
    op.create_index("ix_research_notes_source_document_id", "research_notes", ["source_document_id"])
    op.create_index("ix_research_notes_news_event_id", "research_notes", ["news_event_id"])

    # ── thesis_changes ────────────────────────────────────────────────────────
    op.create_table(
        "thesis_changes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("thesis_id", UUID(as_uuid=True), sa.ForeignKey("investment_theses.id")),
        sa.Column("change_type", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("evidence_news_event_id", UUID(as_uuid=True), sa.ForeignKey("news_events.id")),
        sa.Column("evidence_research_note_id", UUID(as_uuid=True), sa.ForeignKey("research_notes.id")),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamp_columns(),
    )
    op.create_index("ix_thesis_changes_company_id", "thesis_changes", ["company_id"])
    op.create_index("ix_thesis_changes_thesis_id", "thesis_changes", ["thesis_id"])
    op.create_index("ix_thesis_changes_evidence_news_event_id", "thesis_changes", ["evidence_news_event_id"])
    op.create_index("ix_thesis_changes_evidence_research_note_id", "thesis_changes", ["evidence_research_note_id"])

    # ── catalysts ─────────────────────────────────────────────────────────────
    op.create_table(
        "catalysts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("catalyst_type", sa.String(50), nullable=False, server_default="other"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("expected_by", sa.String(255)),
        sa.Column("news_event_id", UUID(as_uuid=True), sa.ForeignKey("news_events.id")),
        sa.Column("source_document_id", UUID(as_uuid=True), sa.ForeignKey("source_documents.id")),
        *_timestamp_columns(),
    )
    op.create_index("ix_catalysts_company_id", "catalysts", ["company_id"])
    op.create_index("ix_catalysts_status", "catalysts", ["status"])
    op.create_index("ix_catalysts_news_event_id", "catalysts", ["news_event_id"])
    op.create_index("ix_catalysts_source_document_id", "catalysts", ["source_document_id"])

    # ── risk_observations ─────────────────────────────────────────────────────
    op.create_table(
        "risk_observations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("risk_type", sa.String(50), nullable=False, server_default="other"),
        sa.Column("severity", sa.String(20), nullable=False, server_default="unclassified"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("news_event_id", UUID(as_uuid=True), sa.ForeignKey("news_events.id")),
        sa.Column("source_document_id", UUID(as_uuid=True), sa.ForeignKey("source_documents.id")),
        *_timestamp_columns(),
    )
    op.create_index("ix_risk_observations_company_id", "risk_observations", ["company_id"])
    op.create_index("ix_risk_observations_status", "risk_observations", ["status"])
    op.create_index("ix_risk_observations_news_event_id", "risk_observations", ["news_event_id"])
    op.create_index("ix_risk_observations_source_document_id", "risk_observations", ["source_document_id"])

    # ── source_reliability ────────────────────────────────────────────────────
    op.create_table(
        "source_reliability",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_name", sa.String(50), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("research_only", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("successful_fetches", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_fetches", sa.Integer, nullable=False, server_default="0"),
        sa.Column("stale_feed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("duplicate_rate", sa.Numeric(5, 4)),
        sa.Column("company_relevance_rate", sa.Numeric(5, 4)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_failure_at", sa.DateTime(timezone=True)),
        *_timestamp_columns(),
    )
    op.create_index("ix_source_reliability_source_name", "source_reliability", ["source_name"])


def downgrade() -> None:
    op.drop_table("source_reliability")
    op.drop_table("risk_observations")
    op.drop_table("catalysts")
    op.drop_table("thesis_changes")
    op.drop_table("research_notes")
    op.drop_table("news_company_links")
    op.drop_table("news_event_items")
    op.drop_table("news_events")
    op.drop_table("news_items")
    op.drop_table("company_aliases")
