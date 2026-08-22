"""Phase 7E: audited, append-only active thesis creation."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import Company, FinancialResult, SourceDocument
from investing_agent.db.repositories.thesis import ThesisRepository
from investing_agent.schemas.thesis import InvestmentThesisCreate, InvestmentThesisUpdate

THESIS_MODEL_VERSION = "active-thesis-v1"


class ThesisEvidenceError(ValueError):
    """The requested thesis would cite unavailable or unverified evidence."""


async def create_active_thesis(
    session: AsyncSession,
    *,
    user_id: str,
    symbol: str,
    as_of: datetime,
    thesis: str,
    drivers: list[str],
    risks: list[str],
    catalysts: list[str],
    invalidation_conditions: list[str],
    financial_result_ids: list[uuid.UUID],
    source_document_ids: list[uuid.UUID],
) -> object:
    """Create one immutable thesis version from only PIT-valid primary facts.

    Financial-result links must be VERIFIED and available at the cutoff.  Any
    separately linked document must belong to the company and be available by
    the cutoff.  This function deliberately has no policy or execution path.
    """
    company = (await session.execute(select(Company).where(Company.symbol == symbol.upper()))).scalar_one_or_none()
    if company is None:
        raise ThesisEvidenceError("company_not_resolved")
    if not (drivers and risks and catalysts and invalidation_conditions):
        raise ThesisEvidenceError("drivers_risks_catalysts_and_invalidation_conditions_are_all_required")
    if not financial_result_ids:
        raise ThesisEvidenceError("at_least_one_verified_financial_result_is_required")

    evidence_refs: list[dict[str, str]] = []
    results = list((await session.execute(select(FinancialResult).where(FinancialResult.id.in_(financial_result_ids)))).scalars())
    if len(results) != len(set(financial_result_ids)):
        raise ThesisEvidenceError("financial_result_not_found")
    for result in results:
        if result.company_id != company.id or result.verification_status != "verified":
            raise ThesisEvidenceError("financial_result_must_be_verified_and_belong_to_company")
        if result.available_at > as_of:
            raise ThesisEvidenceError("financial_result_not_pit_valid_at_as_of")
        evidence_refs.append({
            "type": "financial_result", "id": str(result.id),
            "source_document_id": str(result.source_document_id) if result.source_document_id else "",
            "available_at": result.available_at.isoformat(),
        })

    documents = list((await session.execute(select(SourceDocument).where(SourceDocument.id.in_(source_document_ids)))).scalars()) if source_document_ids else []
    if len(documents) != len(set(source_document_ids)):
        raise ThesisEvidenceError("source_document_not_found")
    for document in documents:
        if document.company_id != company.id:
            raise ThesisEvidenceError("source_document_must_belong_to_company")
        if document.available_at > as_of:
            raise ThesisEvidenceError("source_document_not_pit_valid_at_as_of")
        evidence_refs.append({
            "type": "source_document", "id": str(document.id),
            "source_url": document.source_url or "",
            "available_at": document.available_at.isoformat(),
        })

    payload = dict(
            company_id=company.id,
            symbol=company.symbol,
            status="active",
            thesis=thesis,
            buy_reasons=drivers,
            risk_factors=risks,
            catalysts=catalysts,
            invalidation_conditions=invalidation_conditions,
            as_of=as_of,
            thesis_model_version=THESIS_MODEL_VERSION,
            evidence_refs=evidence_refs,
    )
    repository = ThesisRepository(session)
    current = await repository.get_active(user_id, company.symbol)
    if current is not None:
        # A refresh is an append-only successor, never an in-place rewrite.
        return await repository.update(current, InvestmentThesisUpdate(**payload))
    return await repository.create(user_id, InvestmentThesisCreate(**payload))
