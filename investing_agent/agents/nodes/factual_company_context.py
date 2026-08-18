from __future__ import annotations

"""factual_company_context node: serve verified company facts from PostgreSQL.

Architecture (Phase 3A):
    NSE/BSE ──[sync-corporate-actions / sync-financial-results CLI]──► PostgreSQL
                                                                             │
                                                     factual_company_context_node reads here
                                                                             │
                                                              LangGraph state["company_facts"]

This node NEVER calls NSEDataSource/BSEDataSource directly — ingestion is a
separate, explicitly triggered pipeline (CLI/API), exactly like
portfolio_node never calls the broker directly. Every fact carries its
verification_status and source_document_id so downstream nodes/UI can
distinguish a cross-checked FACT from an unverified JSON hint rather than
treating them as equally trustworthy.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.agents.state import InvestmentState
from investing_agent.config.logging import get_logger
from investing_agent.db.repositories.company import CompanyRepository
from investing_agent.db.repositories.company_research import (
    CapacityUpdateRepository,
    ManagementCommentaryRepository,
    ManagementGuidanceRepository,
    OperationalMetricRepository,
    OrderBookSnapshotRepository,
    SegmentMetricRepository,
)
from investing_agent.db.repositories.corporate_action import CorporateActionRepository
from investing_agent.db.repositories.financial import FinancialResultRepository

log = get_logger(__name__)

# Phase 3B rows use ExtractionMixin's verification_status vocabulary
# (UNVERIFIED|HUMAN_VERIFIED|DOCUMENT_VERIFIED|REJECTED) — deliberately
# different from Phase 3A's VerificationMixin ("unverified"/"verified"), so
# this set is distinct from the "verified" check used for financials/actions.
_CONFIRMED_STATUSES = {"HUMAN_VERIFIED", "DOCUMENT_VERIFIED"}


async def factual_company_context_node(
    state: InvestmentState,
    session: AsyncSession,
) -> dict[str, Any]:
    symbols = state.get("symbols", [])
    company_facts = dict(state.get("company_facts", {}))
    evidence = list(state.get("evidence", []))
    freshness = dict(state.get("data_freshness", {}))

    company_repo = CompanyRepository(session)
    financial_repo = FinancialResultRepository(session)
    action_repo = CorporateActionRepository(session)
    order_book_repo = OrderBookSnapshotRepository(session)
    guidance_repo = ManagementGuidanceRepository(session)
    segment_repo = SegmentMetricRepository(session)
    operational_repo = OperationalMetricRepository(session)
    capacity_repo = CapacityUpdateRepository(session)
    commentary_repo = ManagementCommentaryRepository(session)

    latest_available_at = None

    for symbol in symbols:
        company = await company_repo.get_by_symbol(symbol)
        if not company:
            log.info("factual_company_context.no_company", symbol=symbol)
            continue

        financial_rows = await financial_repo.list_by_company(company.id)
        action_rows = await action_repo.list_by_company(company.id)
        order_book_rows = await order_book_repo.list_by_company(company.id)
        guidance_rows = await guidance_repo.list_by_company(company.id)
        segment_rows = await segment_repo.list_by_company(company.id)
        operational_rows = await operational_repo.list_by_company(company.id)
        capacity_rows = await capacity_repo.list_by_company(company.id)
        commentary_rows = await commentary_repo.list_by_company(company.id)

        company_facts.setdefault(symbol, {})
        company_facts[symbol]["financials"] = [_financial_to_dict(r) for r in financial_rows]
        company_facts[symbol]["corporate_actions"] = [_action_to_dict(r) for r in action_rows]
        company_facts[symbol]["order_book"] = [_order_book_to_dict(r) for r in order_book_rows]
        company_facts[symbol]["guidance"] = [_guidance_to_dict(r) for r in guidance_rows]
        company_facts[symbol]["segment_metrics"] = [
            _segment_metric_to_dict(r) for r in segment_rows
        ]
        company_facts[symbol]["operational_metrics"] = [
            _operational_metric_to_dict(r) for r in operational_rows
        ]
        company_facts[symbol]["capacity_updates"] = [
            _capacity_update_to_dict(r) for r in capacity_rows
        ]
        company_facts[symbol]["commentary"] = [_commentary_to_dict(r) for r in commentary_rows]

        for row in financial_rows:
            evidence.append(_financial_evidence(symbol, row))
            if latest_available_at is None or row.available_at > latest_available_at:
                latest_available_at = row.available_at
        for row in action_rows:
            evidence.append(_action_evidence(symbol, row))
            if latest_available_at is None or row.available_at > latest_available_at:
                latest_available_at = row.available_at
        for row in (
            order_book_rows + guidance_rows + segment_rows
            + operational_rows + capacity_rows + commentary_rows
        ):
            evidence.append(_extraction_evidence(symbol, row))
            if latest_available_at is None or row.available_at > latest_available_at:
                latest_available_at = row.available_at

        log.info(
            "factual_company_context.served_from_db",
            symbol=symbol,
            financials_count=len(financial_rows),
            corporate_actions_count=len(action_rows),
            order_book_count=len(order_book_rows),
            guidance_count=len(guidance_rows),
            segment_metrics_count=len(segment_rows),
            operational_metrics_count=len(operational_rows),
            capacity_updates_count=len(capacity_rows),
            commentary_count=len(commentary_rows),
        )

    if latest_available_at is not None:
        freshness["fundamentals"] = latest_available_at.isoformat()

    return {
        "company_facts": company_facts,
        "evidence": evidence,
        "data_freshness": freshness,
    }


def _financial_to_dict(row: Any) -> dict[str, Any]:
    return {
        "period_id": str(row.period_id),
        "statement_scope": row.statement_scope,
        "reporting_basis": row.reporting_basis,
        "result_date": row.result_date.isoformat() if row.result_date else None,
        "revenue": float(row.revenue) if row.revenue is not None else None,
        "pbt": float(row.pbt) if row.pbt is not None else None,
        "pat": float(row.pat) if row.pat is not None else None,
        "eps_basic": float(row.eps_basic) if row.eps_basic is not None else None,
        "unit_scale": row.unit_scale,
        "currency": row.currency,
        "is_audited": row.is_audited,
        "verification_status": row.verification_status,
        "source_document_id": str(row.source_document_id) if row.source_document_id else None,
        "source_type": row.source_type,
    }


def _action_to_dict(row: Any) -> dict[str, Any]:
    return {
        "action_type": row.action_type,
        "event_date": row.event_date.isoformat(),
        "ex_date": row.ex_date.isoformat() if row.ex_date else None,
        "record_date": row.record_date.isoformat() if row.record_date else None,
        "payment_date": row.payment_date.isoformat() if row.payment_date else None,
        "amount": float(row.amount) if row.amount is not None else None,
        "dividend_type": row.dividend_type,
        "verification_status": row.verification_status,
        "source_document_id": str(row.source_document_id) if row.source_document_id else None,
        "source_type": row.source_type,
    }


def _financial_evidence(symbol: str, row: Any) -> dict[str, Any]:
    return {
        "source": row.source_type,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "url": row.source_url,
        "tier": 1,
        "category": row.data_category,
        "excerpt": (
            f"{symbol} {row.statement_scope} {row.reporting_basis} result "
            f"(result_date={row.result_date}): revenue={row.revenue}, pat={row.pat}"
        ),
        "is_confirmed": row.verification_status == "verified",
    }


def _action_evidence(symbol: str, row: Any) -> dict[str, Any]:
    return {
        "source": row.source_type,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "url": row.source_url,
        "tier": 1,
        "category": row.data_category,
        "excerpt": f"{symbol} {row.action_type}: ex_date={row.ex_date}, amount={row.amount}",
        "is_confirmed": row.verification_status == "verified",
    }


# ── Phase 3B: primary-source facts ────────────────────────────────────────

def _order_book_to_dict(row: Any) -> dict[str, Any]:
    return {
        "as_of_date": row.as_of_date.isoformat(),
        "order_book_value": float(row.order_book_value),
        "currency": row.currency,
        "unit_scale": row.unit_scale,
        "segment": row.segment,
        "book_to_bill_ratio": (
            float(row.book_to_bill_ratio) if row.book_to_bill_ratio is not None else None
        ),
        "expected_execution_period": row.expected_execution_period,
        "verification_status": row.verification_status,
        "source_document_id": str(row.source_document_id),
    }


def _guidance_to_dict(row: Any) -> dict[str, Any]:
    return {
        "fiscal_year": row.fiscal_year,
        "guidance_type": row.guidance_type,
        "metric_label": row.metric_label,
        "guidance_value_text": row.guidance_value_text,
        "guidance_low": float(row.guidance_low) if row.guidance_low is not None else None,
        "guidance_high": float(row.guidance_high) if row.guidance_high is not None else None,
        "period_label": row.period_label,
        "given_by": row.given_by,
        "verification_status": row.verification_status,
        "source_document_id": str(row.source_document_id),
    }


def _segment_metric_to_dict(row: Any) -> dict[str, Any]:
    return {
        "segment_name": row.segment_name,
        "metric_type": row.metric_type,
        "value": float(row.value),
        "unit_scale": row.unit_scale,
        "currency": row.currency,
        "verification_status": row.verification_status,
        "source_document_id": str(row.source_document_id),
    }


def _operational_metric_to_dict(row: Any) -> dict[str, Any]:
    return {
        "metric_name": row.metric_name,
        "value": float(row.value),
        "unit": row.unit,
        "as_of_date": row.as_of_date.isoformat() if row.as_of_date else None,
        "verification_status": row.verification_status,
        "source_document_id": str(row.source_document_id),
    }


def _capacity_update_to_dict(row: Any) -> dict[str, Any]:
    return {
        "update_type": row.update_type,
        "location": row.location,
        "capacity_before": float(row.capacity_before) if row.capacity_before is not None else None,
        "capacity_after": float(row.capacity_after) if row.capacity_after is not None else None,
        "unit": row.unit,
        "announced_date": row.announced_date.isoformat() if row.announced_date else None,
        "expected_completion": row.expected_completion,
        "verification_status": row.verification_status,
        "source_document_id": str(row.source_document_id),
    }


def _commentary_to_dict(row: Any) -> dict[str, Any]:
    return {
        "speaker": row.speaker,
        "topic": row.topic,
        "quote": row.quote,
        "verification_status": row.verification_status,
        "source_document_id": str(row.source_document_id),
    }


def _extraction_evidence(symbol: str, row: Any) -> dict[str, Any]:
    """Shared evidence builder for every Phase 3B (ExtractionMixin) fact
    type — they all carry source_quote/source_page/verification_status in
    the same shape, unlike the Phase 3A financial/action rows above."""
    excerpt = row.source_quote or f"{symbol} {type(row).__name__}"
    return {
        "source": row.source_type,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "url": row.source_url,
        "tier": 1,
        "category": row.data_category,
        "excerpt": excerpt,
        "is_confirmed": row.verification_status in _CONFIRMED_STATUSES,
    }
