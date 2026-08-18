from __future__ import annotations

"""Dividend / result calendar API routes.

Answers "what dividends are expected from my holdings in the next N days"
and "when do my companies next report results" directly from PostgreSQL —
no live NSE/BSE call happens on a calendar read.
"""

from datetime import datetime

from fastapi import APIRouter, Query

from investing_agent.app.dependencies import DbSession, SettingsDep
from investing_agent.db.repositories.corporate_action import CorporateActionRepository
from investing_agent.db.repositories.portfolio import PortfolioRepository
from investing_agent.schemas.corporate_actions import DividendCalendarItem, ResultCalendarItem

router = APIRouter(prefix="/calendar", tags=["calendar"])


async def _portfolio_symbols(settings: SettingsDep, db: DbSession) -> set[str]:
    repo = PortfolioRepository(db)
    symbols = await repo.get_portfolio_symbols(settings.default_user_id)
    return {s.upper() for s in symbols}


@router.get("/dividends", response_model=list[DividendCalendarItem])
async def get_dividend_calendar(
    settings: SettingsDep,
    db: DbSession,
    days: int = Query(30, ge=1, le=365),
    as_of: datetime | None = Query(None, description="Point-in-time cutoff for backtesting."),
) -> list[DividendCalendarItem]:
    action_repo = CorporateActionRepository(db)
    rows = await action_repo.get_dividend_calendar(days=days, as_of=as_of)
    portfolio_symbols = await _portfolio_symbols(settings, db)
    today = (as_of or datetime.now()).date()

    return [
        DividendCalendarItem(
            symbol=row.symbol,
            company_id=row.company_id,
            dividend_type=row.dividend_type,
            amount=row.amount,
            announced_date=row.announced_date,
            ex_date=row.ex_date,
            record_date=row.record_date,
            payment_date=row.payment_date,
            days_to_ex_date=(row.ex_date - today).days if row.ex_date else None,
            verification_status=row.verification_status,
            is_in_portfolio=row.symbol.upper() in portfolio_symbols,
        )
        for row in rows
    ]


@router.get("/results", response_model=list[ResultCalendarItem])
async def get_result_calendar(
    settings: SettingsDep,
    db: DbSession,
    as_of: datetime | None = Query(None, description="Point-in-time cutoff for backtesting."),
) -> list[ResultCalendarItem]:
    action_repo = CorporateActionRepository(db)
    rows = await action_repo.get_result_calendar(as_of=as_of)
    portfolio_symbols = await _portfolio_symbols(settings, db)
    today = (as_of or datetime.now()).date()

    return [
        ResultCalendarItem(
            symbol=row.symbol,
            company_id=row.company_id,
            board_meeting_announced_at=row.board_meeting_announced_at,
            board_meeting_date=row.board_meeting_date,
            expected_result_date=row.expected_result_date,
            actual_result_published_at=row.actual_result_published_at,
            days_to_expected_result=(
                (row.expected_result_date - today).days if row.expected_result_date else None
            ),
            verification_status=row.verification_status,
            is_in_portfolio=row.symbol.upper() in portfolio_symbols,
        )
        for row in rows
    ]
