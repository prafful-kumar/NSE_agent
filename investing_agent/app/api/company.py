from __future__ import annotations

"""Company API routes."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from investing_agent.app.dependencies import DbSession, SettingsDep
from investing_agent.db.repositories.company import CompanyRepository
from investing_agent.db.repositories.corporate_action import CorporateActionRepository
from investing_agent.db.repositories.financial import FinancialResultRepository
from investing_agent.db.repositories.source_document import SourceDocumentRepository
from investing_agent.db.repositories.thesis import ThesisRepository
from investing_agent.schemas.company import CompanyCreate, CompanyRead
from investing_agent.schemas.corporate_actions import CorporateActionRead
from investing_agent.schemas.financials import FinancialResultRead
from investing_agent.schemas.source_documents import SourceDocumentRead
from investing_agent.schemas.thesis import (
    InvestmentThesisCreate,
    InvestmentThesisRead,
    InvestmentThesisUpdate,
)

router = APIRouter(prefix="/company", tags=["company"])


async def _get_company_or_404(symbol: str, db: DbSession):
    repo = CompanyRepository(db)
    company = await repo.get_by_symbol(symbol)
    if not company:
        raise HTTPException(status_code=404, detail=f"Company {symbol.upper()} not found")
    return company


@router.get("/{symbol}", response_model=CompanyRead)
async def get_company(symbol: str, db: DbSession) -> CompanyRead:
    repo = CompanyRepository(db)
    company = await repo.get_by_symbol(symbol)
    if not company:
        raise HTTPException(status_code=404, detail=f"Company {symbol.upper()} not found")
    return CompanyRead.model_validate(company)


@router.post("", response_model=CompanyRead, status_code=201)
async def create_company(body: CompanyCreate, db: DbSession) -> CompanyRead:
    repo = CompanyRepository(db)
    company = await repo.upsert(body)
    return CompanyRead.model_validate(company)


@router.get("/{symbol}/thesis", response_model=InvestmentThesisRead)
async def get_thesis(symbol: str, settings: SettingsDep, db: DbSession) -> InvestmentThesisRead:
    repo = ThesisRepository(db)
    thesis = await repo.get_active(settings.default_user_id, symbol)
    if not thesis:
        raise HTTPException(
            status_code=404,
            detail=f"No active thesis for {symbol.upper()}",
        )
    return InvestmentThesisRead.model_validate(thesis)


@router.post("/{symbol}/thesis", response_model=InvestmentThesisRead, status_code=201)
async def create_thesis(
    symbol: str,
    body: InvestmentThesisCreate,
    settings: SettingsDep,
    db: DbSession,
) -> InvestmentThesisRead:
    body_with_symbol = InvestmentThesisCreate(**{**body.model_dump(), "symbol": symbol})
    repo = ThesisRepository(db)
    thesis = await repo.create(settings.default_user_id, body_with_symbol)
    return InvestmentThesisRead.model_validate(thesis)


@router.patch("/{symbol}/thesis", response_model=InvestmentThesisRead)
async def update_thesis(
    symbol: str,
    body: InvestmentThesisUpdate,
    settings: SettingsDep,
    db: DbSession,
) -> InvestmentThesisRead:
    repo = ThesisRepository(db)
    thesis = await repo.get_active(settings.default_user_id, symbol)
    if not thesis:
        raise HTTPException(
            status_code=404,
            detail=f"No active thesis for {symbol.upper()}",
        )
    updated = await repo.update(thesis, body)
    return InvestmentThesisRead.model_validate(updated)


@router.get("/{symbol}/financials", response_model=list[FinancialResultRead])
async def get_financials(
    symbol: str,
    db: DbSession,
    as_of: datetime | None = Query(
        None, description="Point-in-time cutoff (ISO datetime). Omit for latest known."
    ),
) -> list[FinancialResultRead]:
    """Verified + unverified financial results known for this company.
    Check verification_status before treating a value as confirmed FACT."""
    company = await _get_company_or_404(symbol, db)
    repo = FinancialResultRepository(db)
    rows = (
        await repo.list_by_company_as_of(company.id, as_of)
        if as_of
        else await repo.list_by_company(company.id)
    )
    return [FinancialResultRead.model_validate(r) for r in rows]


@router.get("/{symbol}/corporate-actions", response_model=list[CorporateActionRead])
async def get_corporate_actions(
    symbol: str,
    db: DbSession,
    as_of: datetime | None = Query(None, description="Point-in-time cutoff (ISO datetime)."),
) -> list[CorporateActionRead]:
    company = await _get_company_or_404(symbol, db)
    repo = CorporateActionRepository(db)
    rows = (
        await repo.list_by_company_as_of(company.id, as_of)
        if as_of
        else await repo.list_by_company(company.id)
    )
    return [CorporateActionRead.model_validate(r) for r in rows]


@router.get("/{symbol}/filings", response_model=list[SourceDocumentRead])
async def get_filings(symbol: str, db: DbSession) -> list[SourceDocumentRead]:
    """Raw archived source documents for this company — the Tier-1 ground
    truth every normalized fact traces back to via source_document_id."""
    company = await _get_company_or_404(symbol, db)
    repo = SourceDocumentRepository(db)
    rows = await repo.list_by_company(company.id)
    return [SourceDocumentRead.model_validate(r) for r in rows]
