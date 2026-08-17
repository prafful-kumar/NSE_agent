from __future__ import annotations
"""Company API routes."""

from fastapi import APIRouter, HTTPException

from investing_agent.app.dependencies import DbSession, SettingsDep
from investing_agent.db.repositories.company import CompanyRepository
from investing_agent.db.repositories.thesis import ThesisRepository
from investing_agent.schemas.company import CompanyCreate, CompanyRead
from investing_agent.schemas.thesis import (
    InvestmentThesisCreate,
    InvestmentThesisRead,
    InvestmentThesisUpdate,
)

router = APIRouter(prefix="/company", tags=["company"])


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
