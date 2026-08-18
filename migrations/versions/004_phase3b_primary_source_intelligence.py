"""Phase 3B: primary-source company intelligence (order_book_snapshots,
management_guidance, segment_metrics, operational_metrics, capacity_updates,
management_commentary, extraction_candidates).

Revision ID: 004
Revises: 003
Create Date: 2026-08-18
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _provenance_columns() -> list[sa.Column]:
    return [
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("data_category", sa.String(20), nullable=False, server_default="fact"),
        sa.Column("confidence", sa.Numeric(5, 4)),
    ]


def _extraction_columns() -> list[sa.Column]:
    """Columns matching ExtractionMixin. source_document_id FK is added
    separately in each op.create_table call since source_documents already
    exists (migration 003)."""
    return [
        sa.Column("source_document_id", UUID(as_uuid=True), sa.ForeignKey("source_documents.id"), nullable=False),
        sa.Column("source_page", sa.Integer),
        sa.Column("source_section", sa.String(255)),
        sa.Column("source_quote", sa.Text),
        sa.Column("extracted_at", sa.DateTime(timezone=True)),
        sa.Column("extraction_method", sa.String(20), nullable=False, server_default="MANUAL"),
        sa.Column("extractor_version", sa.String(50)),
        sa.Column("verification_status", sa.String(20), nullable=False, server_default="UNVERIFIED"),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("verified_by", sa.String(100)),
    ]


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    # ── order_book_snapshots ─────────────────────────────────────────────────
    op.create_table(
        "order_book_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("order_book_value", sa.Numeric(20, 2), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="INR"),
        sa.Column("unit_scale", sa.String(10), nullable=False, server_default="UNRESOLVED"),
        sa.Column("segment", sa.String(100)),
        sa.Column("book_to_bill_ratio", sa.Numeric(8, 4)),
        sa.Column("expected_execution_period", sa.String(255)),
        sa.Column("notes", sa.Text),
        *_provenance_columns(),
        *_extraction_columns(),
        *_timestamp_columns(),
    )
    op.create_index("ix_order_book_snapshots_company_id", "order_book_snapshots", ["company_id"])
    op.create_index("ix_order_book_snapshots_symbol", "order_book_snapshots", ["symbol"])
    op.create_index("ix_order_book_snapshots_as_of_date", "order_book_snapshots", ["as_of_date"])
    op.create_index("ix_order_book_snapshots_available_at", "order_book_snapshots", ["available_at"])
    op.create_index("ix_order_book_snapshots_source_document_id", "order_book_snapshots", ["source_document_id"])
    op.create_index("ix_order_book_snapshots_verification_status", "order_book_snapshots", ["verification_status"])

    # ── management_guidance ──────────────────────────────────────────────────
    op.create_table(
        "management_guidance",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("fiscal_year", sa.Integer, nullable=False),
        sa.Column("guidance_type", sa.String(50), nullable=False),
        sa.Column("metric_label", sa.String(255), nullable=False),
        sa.Column("guidance_value_text", sa.Text, nullable=False),
        sa.Column("guidance_low", sa.Numeric(18, 4)),
        sa.Column("guidance_high", sa.Numeric(18, 4)),
        sa.Column("period_label", sa.String(50)),
        sa.Column("given_by", sa.String(255)),
        sa.Column("context", sa.Text),
        *_provenance_columns(),
        *_extraction_columns(),
        *_timestamp_columns(),
    )
    op.create_index("ix_management_guidance_company_id", "management_guidance", ["company_id"])
    op.create_index("ix_management_guidance_symbol", "management_guidance", ["symbol"])
    op.create_index("ix_management_guidance_fiscal_year", "management_guidance", ["fiscal_year"])
    op.create_index("ix_management_guidance_available_at", "management_guidance", ["available_at"])
    op.create_index("ix_management_guidance_source_document_id", "management_guidance", ["source_document_id"])
    op.create_index("ix_management_guidance_verification_status", "management_guidance", ["verification_status"])

    # ── segment_metrics ───────────────────────────────────────────────────────
    op.create_table(
        "segment_metrics",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("period_id", UUID(as_uuid=True), sa.ForeignKey("financial_periods.id")),
        sa.Column("segment_name", sa.String(255), nullable=False),
        sa.Column("metric_type", sa.String(50), nullable=False),
        sa.Column("value", sa.Numeric(20, 2), nullable=False),
        sa.Column("unit_scale", sa.String(10), nullable=False, server_default="UNRESOLVED"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="INR"),
        *_provenance_columns(),
        *_extraction_columns(),
        *_timestamp_columns(),
    )
    op.create_index("ix_segment_metrics_company_id", "segment_metrics", ["company_id"])
    op.create_index("ix_segment_metrics_symbol", "segment_metrics", ["symbol"])
    op.create_index("ix_segment_metrics_period_id", "segment_metrics", ["period_id"])
    op.create_index("ix_segment_metrics_available_at", "segment_metrics", ["available_at"])
    op.create_index("ix_segment_metrics_source_document_id", "segment_metrics", ["source_document_id"])
    op.create_index("ix_segment_metrics_verification_status", "segment_metrics", ["verification_status"])

    # ── operational_metrics ───────────────────────────────────────────────────
    op.create_table(
        "operational_metrics",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("period_id", UUID(as_uuid=True), sa.ForeignKey("financial_periods.id")),
        sa.Column("metric_name", sa.String(255), nullable=False),
        sa.Column("value", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit", sa.String(50)),
        sa.Column("as_of_date", sa.Date),
        *_provenance_columns(),
        *_extraction_columns(),
        *_timestamp_columns(),
    )
    op.create_index("ix_operational_metrics_company_id", "operational_metrics", ["company_id"])
    op.create_index("ix_operational_metrics_symbol", "operational_metrics", ["symbol"])
    op.create_index("ix_operational_metrics_period_id", "operational_metrics", ["period_id"])
    op.create_index("ix_operational_metrics_as_of_date", "operational_metrics", ["as_of_date"])
    op.create_index("ix_operational_metrics_available_at", "operational_metrics", ["available_at"])
    op.create_index("ix_operational_metrics_source_document_id", "operational_metrics", ["source_document_id"])
    op.create_index("ix_operational_metrics_verification_status", "operational_metrics", ["verification_status"])

    # ── capacity_updates ──────────────────────────────────────────────────────
    op.create_table(
        "capacity_updates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("update_type", sa.String(50), nullable=False),
        sa.Column("location", sa.String(255)),
        sa.Column("capacity_before", sa.Numeric(20, 4)),
        sa.Column("capacity_after", sa.Numeric(20, 4)),
        sa.Column("unit", sa.String(50)),
        sa.Column("announced_date", sa.Date),
        sa.Column("expected_completion", sa.String(255)),
        sa.Column("description", sa.Text),
        *_provenance_columns(),
        *_extraction_columns(),
        *_timestamp_columns(),
    )
    op.create_index("ix_capacity_updates_company_id", "capacity_updates", ["company_id"])
    op.create_index("ix_capacity_updates_symbol", "capacity_updates", ["symbol"])
    op.create_index("ix_capacity_updates_announced_date", "capacity_updates", ["announced_date"])
    op.create_index("ix_capacity_updates_available_at", "capacity_updates", ["available_at"])
    op.create_index("ix_capacity_updates_source_document_id", "capacity_updates", ["source_document_id"])
    op.create_index("ix_capacity_updates_verification_status", "capacity_updates", ["verification_status"])

    # ── management_commentary ────────────────────────────────────────────────
    op.create_table(
        "management_commentary",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("period_id", UUID(as_uuid=True), sa.ForeignKey("financial_periods.id")),
        sa.Column("speaker", sa.String(255)),
        sa.Column("topic", sa.String(255)),
        sa.Column("quote", sa.Text, nullable=False),
        sa.Column("context", sa.Text),
        *_provenance_columns(),
        *_extraction_columns(),
        *_timestamp_columns(),
    )
    op.create_index("ix_management_commentary_company_id", "management_commentary", ["company_id"])
    op.create_index("ix_management_commentary_symbol", "management_commentary", ["symbol"])
    op.create_index("ix_management_commentary_period_id", "management_commentary", ["period_id"])
    op.create_index("ix_management_commentary_available_at", "management_commentary", ["available_at"])
    op.create_index("ix_management_commentary_source_document_id", "management_commentary", ["source_document_id"])
    op.create_index("ix_management_commentary_verification_status", "management_commentary", ["verification_status"])

    # ── extraction_candidates (staging, unused by any pipeline in Phase 3B) ──
    op.create_table(
        "extraction_candidates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("source_document_id", UUID(as_uuid=True), sa.ForeignKey("source_documents.id"), nullable=False),
        sa.Column("candidate_type", sa.String(50), nullable=False),
        sa.Column("extracted_payload", JSONB, nullable=False),
        sa.Column("extraction_method", sa.String(20), nullable=False, server_default="LLM_ASSISTED"),
        sa.Column("extractor_version", sa.String(50)),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("source_page", sa.Integer),
        sa.Column("source_quote", sa.Text),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(100)),
        sa.Column("resulting_record_id", UUID(as_uuid=True)),
        *_timestamp_columns(),
    )
    op.create_index("ix_extraction_candidates_company_id", "extraction_candidates", ["company_id"])
    op.create_index("ix_extraction_candidates_source_document_id", "extraction_candidates", ["source_document_id"])
    op.create_index("ix_extraction_candidates_review_status", "extraction_candidates", ["review_status"])


def downgrade() -> None:
    op.drop_table("extraction_candidates")
    op.drop_table("management_commentary")
    op.drop_table("capacity_updates")
    op.drop_table("operational_metrics")
    op.drop_table("segment_metrics")
    op.drop_table("management_guidance")
    op.drop_table("order_book_snapshots")
