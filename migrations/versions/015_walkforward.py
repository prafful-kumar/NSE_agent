"""Phase 6C: walk-forward simulation v1.

Adds three tables:
- walk_forward_runs: one batch of walk-forward decisions/outcomes for a
  broker account.
- walk_forward_decisions: one frozen decision (ACTUAL | HOLD_BASELINE |
  AGENT) for one symbol as of one decision_at. Immutable -- no update
  method is ever offered by the repository; the unique constraint on
  (run_id, symbol, decision_at, decision_source) makes re-freezing a no-op.
- walk_forward_outcomes: how one frozen decision played out, scored strictly
  after the decision using future DailyPrice/BenchmarkPrice rows. Every
  numeric field is nullable and left None whenever it cannot be genuinely
  computed (missing price, unresolved corporate action, insufficient
  horizon) rather than fabricated.

Revision ID: 015
Revises: 014
Create Date: 2026-08-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "walk_forward_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "broker_account_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("broker_accounts.id"), nullable=False,
        ),
        sa.Column("strategy_profile", sa.String(20), nullable=False),
        sa.Column("horizons_months", postgresql.JSONB, nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
    )
    op.create_index("ix_walk_forward_runs_broker_account_id", "walk_forward_runs", ["broker_account_id"])
    op.create_index("ix_walk_forward_runs_model_version", "walk_forward_runs", ["model_version"])

    op.create_table(
        "walk_forward_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("walk_forward_runs.id"), nullable=False,
        ),
        sa.Column(
            "broker_account_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("broker_accounts.id"), nullable=False,
        ),
        sa.Column(
            "company_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id"),
        ),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("decision_at", sa.Date, nullable=False),
        sa.Column("feature_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_source", sa.String(20), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("reasoning", sa.Text),
        sa.Column("evidence", postgresql.JSONB, nullable=False),
        sa.Column("quantity_held", sa.Numeric(20, 4), nullable=False),
        sa.Column("average_cost", sa.Numeric(20, 4), nullable=False),
        sa.Column("invested_capital", sa.Numeric(20, 4), nullable=False),
        sa.Column("reconstruction_warnings", postgresql.JSONB, nullable=False),
        sa.Column("data_quality_status", sa.String(30), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "run_id", "symbol", "decision_at", "decision_source",
            name="uq_walk_forward_decision_run_symbol_date_source",
        ),
    )
    op.create_index("ix_walk_forward_decisions_run_id", "walk_forward_decisions", ["run_id"])
    op.create_index("ix_walk_forward_decisions_broker_account_id", "walk_forward_decisions", ["broker_account_id"])
    op.create_index("ix_walk_forward_decisions_company_id", "walk_forward_decisions", ["company_id"])
    op.create_index("ix_walk_forward_decisions_symbol", "walk_forward_decisions", ["symbol"])
    op.create_index("ix_walk_forward_decisions_decision_at", "walk_forward_decisions", ["decision_at"])

    op.create_table(
        "walk_forward_outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "decision_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("walk_forward_decisions.id"), nullable=False, unique=True,
        ),
        sa.Column("entry_price", sa.Numeric(14, 4)),
        sa.Column("entry_price_date", sa.Date),
        sa.Column("price_1m", sa.Numeric(14, 4)),
        sa.Column("price_3m", sa.Numeric(14, 4)),
        sa.Column("price_6m", sa.Numeric(14, 4)),
        sa.Column("price_12m", sa.Numeric(14, 4)),
        sa.Column("price_1m_date", sa.Date),
        sa.Column("price_3m_date", sa.Date),
        sa.Column("price_6m_date", sa.Date),
        sa.Column("price_12m_date", sa.Date),
        sa.Column("stock_return_1m", sa.Numeric(10, 6)),
        sa.Column("stock_return_3m", sa.Numeric(10, 6)),
        sa.Column("stock_return_6m", sa.Numeric(10, 6)),
        sa.Column("stock_return_12m", sa.Numeric(10, 6)),
        sa.Column("benchmark_return_1m", sa.Numeric(10, 6)),
        sa.Column("benchmark_return_3m", sa.Numeric(10, 6)),
        sa.Column("benchmark_return_6m", sa.Numeric(10, 6)),
        sa.Column("benchmark_return_12m", sa.Numeric(10, 6)),
        sa.Column("excess_return_1m", sa.Numeric(10, 6)),
        sa.Column("excess_return_3m", sa.Numeric(10, 6)),
        sa.Column("excess_return_6m", sa.Numeric(10, 6)),
        sa.Column("excess_return_12m", sa.Numeric(10, 6)),
        sa.Column("max_drawdown_pct", sa.Numeric(10, 6)),
        sa.Column("outcome_status", sa.String(12), nullable=False),
        sa.Column("data_quality_notes", postgresql.JSONB, nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_walk_forward_outcomes_decision_id", "walk_forward_outcomes", ["decision_id"])
    op.create_index("ix_walk_forward_outcomes_outcome_status", "walk_forward_outcomes", ["outcome_status"])


def downgrade() -> None:
    op.drop_table("walk_forward_outcomes")
    op.drop_table("walk_forward_decisions")
    op.drop_table("walk_forward_runs")
