"""Phase 3A: company intelligence (source_documents, financial_periods,
financial_results, corporate_actions, company_events, document_versions) +
company exchange/filing identifiers.

Revision ID: 003
Revises: 002
Create Date: 2026-08-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _provenance_columns() -> list[sa.Column]:
    """Columns matching ProvenanceMixin — kept as a helper so every Phase 3A
    table stays byte-for-byte consistent with the ORM mixin."""
    return [
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("data_category", sa.String(20), nullable=False, server_default="fact"),
        sa.Column("confidence", sa.Numeric(5, 4)),
    ]


def _verification_columns() -> list[sa.Column]:
    """Columns matching VerificationMixin. FKs to source_documents.id are
    added separately after that table exists (see upgrade())."""
    return [
        sa.Column("source_document_id", UUID(as_uuid=True)),
        sa.Column("verification_status", sa.String(20), nullable=False, server_default="unverified"),
        sa.Column("verification_document_id", UUID(as_uuid=True)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("verification_method", sa.String(30)),
        sa.Column("verification_notes", sa.Text),
    ]


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    # ── companies: exchange/filing identity (drafted pre-bake-off, never migrated) ──
    op.add_column("companies", sa.Column("nse_symbol", sa.String(30)))
    op.add_column("companies", sa.Column("bse_code", sa.String(20)))
    op.add_column("companies", sa.Column("cin", sa.String(30)))
    op.add_column("companies", sa.Column("website", sa.Text))
    op.add_column("companies", sa.Column("filing_identifiers", JSONB))
    op.add_column("companies", sa.Column("identifiers_updated_at", sa.DateTime(timezone=True)))
    op.create_index("ix_companies_nse_symbol", "companies", ["nse_symbol"])
    op.create_index("ix_companies_bse_code", "companies", ["bse_code"])

    # ── financial_periods (dimension) ────────────────────────────────────────
    op.create_table(
        "financial_periods",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("period_type", sa.String(10), nullable=False),
        sa.Column("fiscal_year", sa.Integer, nullable=False),
        sa.Column("quarter", sa.String(5)),
        sa.Column("period_start", sa.Date),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("label", sa.String(20), nullable=False),
        *_timestamp_columns(),
        sa.UniqueConstraint(
            "company_id", "period_type", "fiscal_year", "quarter",
            name="uq_finperiod_company_type_fy_q",
        ),
    )
    op.create_index("ix_financial_periods_company_id", "financial_periods", ["company_id"])

    # ── source_documents (raw archive; Tier-1 ground truth) ─────────────────
    op.create_table(
        "source_documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("exchange", sa.String(10), nullable=False),
        sa.Column("filing_type", sa.String(40), nullable=False),
        sa.Column("document_type", sa.String(20), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("period_id", UUID(as_uuid=True), sa.ForeignKey("financial_periods.id")),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.Text),
        sa.Column("mime_type", sa.String(100)),
        sa.Column("size_bytes", sa.BigInteger),
        sa.Column("is_current", sa.Boolean, nullable=False, server_default="true"),
        *_provenance_columns(),
        *_timestamp_columns(),
        sa.UniqueConstraint(
            "company_id", "source_url", "content_hash", name="uq_sourcedoc_company_url_hash"
        ),
    )
    op.create_index("ix_source_documents_company_id", "source_documents", ["company_id"])
    op.create_index("ix_source_documents_symbol", "source_documents", ["symbol"])
    op.create_index("ix_source_documents_filing_type", "source_documents", ["filing_type"])
    op.create_index("ix_source_documents_period_id", "source_documents", ["period_id"])
    op.create_index("ix_source_documents_content_hash", "source_documents", ["content_hash"])
    op.create_index("ix_source_documents_source_type", "source_documents", ["source_type"])
    op.create_index("ix_source_documents_available_at", "source_documents", ["available_at"])

    # ── document_versions (amendment/correction chain) ──────────────────────
    op.create_table(
        "document_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_document_id", UUID(as_uuid=True),
            sa.ForeignKey("source_documents.id"), nullable=False,
        ),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("effective_from", sa.Date),
        sa.Column("superseded_by_id", UUID(as_uuid=True), sa.ForeignKey("document_versions.id")),
        sa.Column("notes", sa.Text),
        *_timestamp_columns(),
        sa.UniqueConstraint(
            "source_document_id", "version_number", name="uq_docversion_srcdoc_num"
        ),
    )
    op.create_index("ix_document_versions_source_document_id", "document_versions", ["source_document_id"])

    # ── financial_results (fact table) ───────────────────────────────────────
    op.create_table(
        "financial_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("period_id", UUID(as_uuid=True), sa.ForeignKey("financial_periods.id"), nullable=False),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("statement_scope", sa.String(20), nullable=False, server_default="UNRESOLVED"),
        sa.Column("reporting_basis", sa.String(10), nullable=False, server_default="QUARTER"),
        sa.Column("is_audited", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("result_date", sa.Date),
        sa.Column("currency", sa.String(10), nullable=False, server_default="INR"),
        sa.Column("unit_scale", sa.String(10), nullable=False, server_default="UNRESOLVED"),
        sa.Column("revenue", sa.Numeric(20, 2)),
        sa.Column("ebitda", sa.Numeric(20, 2)),
        sa.Column("ebitda_margin_pct", sa.Numeric(8, 4)),
        sa.Column("ebitda_source", sa.String(10)),
        sa.Column("pbt", sa.Numeric(20, 2)),
        sa.Column("pat", sa.Numeric(20, 2)),
        sa.Column("pat_margin_pct", sa.Numeric(8, 4)),
        sa.Column("eps_basic", sa.Numeric(10, 4)),
        sa.Column("eps_diluted", sa.Numeric(10, 4)),
        sa.Column("total_debt", sa.Numeric(20, 2)),
        sa.Column("cash_equivalents", sa.Numeric(20, 2)),
        sa.Column("operating_cash_flow", sa.Numeric(20, 2)),
        sa.Column("roe_pct", sa.Numeric(8, 4)),
        sa.Column("roce_pct", sa.Numeric(8, 4)),
        sa.Column("other_metrics", JSONB),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_latest", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("supersedes_id", UUID(as_uuid=True), sa.ForeignKey("financial_results.id")),
        *_provenance_columns(),
        *_verification_columns(),
        *_timestamp_columns(),
        sa.UniqueConstraint(
            "period_id", "statement_scope", "reporting_basis", "source_type", "version",
            name="uq_finresult_period_scope_basis_src_ver",
        ),
    )
    op.create_foreign_key(
        "fk_finresult_source_document", "financial_results",
        "source_documents", ["source_document_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_finresult_verification_document", "financial_results",
        "source_documents", ["verification_document_id"], ["id"],
    )
    op.create_index("ix_financial_results_period_id", "financial_results", ["period_id"])
    op.create_index("ix_financial_results_company_id", "financial_results", ["company_id"])
    op.create_index("ix_financial_results_symbol", "financial_results", ["symbol"])
    op.create_index("ix_financial_results_is_latest", "financial_results", ["is_latest"])
    op.create_index("ix_financial_results_source_type", "financial_results", ["source_type"])
    op.create_index("ix_financial_results_available_at", "financial_results", ["available_at"])
    op.create_index("ix_financial_results_verification_status", "financial_results", ["verification_status"])
    op.create_index("ix_financial_results_source_document_id", "financial_results", ["source_document_id"])

    # ── corporate_actions ─────────────────────────────────────────────────────
    op.create_table(
        "corporate_actions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("action_type", sa.String(30), nullable=False),
        sa.Column("announced_date", sa.Date),
        sa.Column("event_date", sa.Date, nullable=False),
        sa.Column("ex_date", sa.Date),
        sa.Column("record_date", sa.Date),
        sa.Column("payment_date", sa.Date),
        sa.Column("agm_date", sa.Date),
        sa.Column("amount", sa.Numeric(18, 4)),
        sa.Column("amount_currency", sa.String(10)),
        sa.Column("ratio", sa.String(50)),
        sa.Column("dividend_type", sa.String(20)),
        sa.Column("details", JSONB),
        sa.Column("is_confirmed", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("board_meeting_announced_at", sa.DateTime(timezone=True)),
        sa.Column("board_meeting_date", sa.Date),
        sa.Column("expected_result_date", sa.Date),
        sa.Column("actual_result_published_at", sa.DateTime(timezone=True)),
        sa.Column("cross_checked", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("cross_check_source", sa.String(50)),
        sa.Column("cross_check_mismatch", JSONB),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_latest", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("supersedes_id", UUID(as_uuid=True), sa.ForeignKey("corporate_actions.id")),
        *_provenance_columns(),
        *_verification_columns(),
        *_timestamp_columns(),
        sa.UniqueConstraint(
            "company_id", "action_type", "event_date", "source_type", "version",
            name="uq_corpaction_company_type_date_src_ver",
        ),
    )
    op.create_foreign_key(
        "fk_corpaction_source_document", "corporate_actions",
        "source_documents", ["source_document_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_corpaction_verification_document", "corporate_actions",
        "source_documents", ["verification_document_id"], ["id"],
    )
    op.create_index("ix_corporate_actions_company_id", "corporate_actions", ["company_id"])
    op.create_index("ix_corporate_actions_symbol", "corporate_actions", ["symbol"])
    op.create_index("ix_corporate_actions_action_type", "corporate_actions", ["action_type"])
    op.create_index("ix_corporate_actions_event_date", "corporate_actions", ["event_date"])
    op.create_index("ix_corporate_actions_ex_date", "corporate_actions", ["ex_date"])
    op.create_index("ix_corporate_actions_is_latest", "corporate_actions", ["is_latest"])
    op.create_index("ix_corporate_actions_source_type", "corporate_actions", ["source_type"])
    op.create_index("ix_corporate_actions_available_at", "corporate_actions", ["available_at"])
    op.create_index("ix_corporate_actions_verification_status", "corporate_actions", ["verification_status"])

    # ── company_events (qualitative timeline) ────────────────────────────────
    op.create_table(
        "company_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("event_date", sa.Date, nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("details", JSONB),
        *_provenance_columns(),
        *_timestamp_columns(),
        sa.UniqueConstraint(
            "company_id", "event_type", "event_date", "content_hash",
            name="uq_company_event_dedup",
        ),
    )
    op.create_index("ix_company_events_company_id", "company_events", ["company_id"])
    op.create_index("ix_company_events_symbol", "company_events", ["symbol"])
    op.create_index("ix_company_events_event_type", "company_events", ["event_type"])
    op.create_index("ix_company_events_event_date", "company_events", ["event_date"])
    op.create_index("ix_company_events_content_hash", "company_events", ["content_hash"])


def downgrade() -> None:
    op.drop_table("company_events")
    op.drop_constraint("fk_corpaction_verification_document", "corporate_actions", type_="foreignkey")
    op.drop_constraint("fk_corpaction_source_document", "corporate_actions", type_="foreignkey")
    op.drop_table("corporate_actions")
    op.drop_constraint("fk_finresult_verification_document", "financial_results", type_="foreignkey")
    op.drop_constraint("fk_finresult_source_document", "financial_results", type_="foreignkey")
    op.drop_table("financial_results")
    op.drop_table("document_versions")
    op.drop_table("source_documents")
    op.drop_table("financial_periods")
    op.drop_index("ix_companies_bse_code", "companies")
    op.drop_index("ix_companies_nse_symbol", "companies")
    op.drop_column("companies", "identifiers_updated_at")
    op.drop_column("companies", "filing_identifiers")
    op.drop_column("companies", "website")
    op.drop_column("companies", "cin")
    op.drop_column("companies", "bse_code")
    op.drop_column("companies", "nse_symbol")
