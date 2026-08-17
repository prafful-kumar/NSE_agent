from __future__ import annotations
"""Watchlist API routes."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from investing_agent.app.dependencies import DbSession, SettingsDep
from investing_agent.db.models import WatchlistItem
from investing_agent.schemas.portfolio import WatchlistItemCreate, WatchlistItemRead

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistItemRead])
async def get_watchlist(settings: SettingsDep, db: DbSession) -> list[WatchlistItemRead]:
    result = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == settings.default_user_id,
            WatchlistItem.is_active.is_(True),
        )
    )
    items = result.scalars().all()
    return [WatchlistItemRead.model_validate(item) for item in items]


@router.post("", response_model=WatchlistItemRead, status_code=201)
async def add_to_watchlist(
    body: WatchlistItemCreate,
    settings: SettingsDep,
    db: DbSession,
) -> WatchlistItemRead:
    # Check for duplicate
    existing_result = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == settings.default_user_id,
            WatchlistItem.symbol == body.symbol.upper(),
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        existing.is_active = True
        existing.reason = body.reason
        await db.flush()
        return WatchlistItemRead.model_validate(existing)

    item = WatchlistItem(
        user_id=settings.default_user_id,
        symbol=body.symbol.upper(),
        reason=body.reason,
        is_active=True,
    )
    db.add(item)
    await db.flush()
    return WatchlistItemRead.model_validate(item)


@router.delete("/{symbol}", status_code=204)
async def remove_from_watchlist(
    symbol: str,
    settings: SettingsDep,
    db: DbSession,
) -> None:
    result = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == settings.default_user_id,
            WatchlistItem.symbol == symbol.upper(),
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail=f"{symbol.upper()} not on watchlist")
    item.is_active = False
    await db.flush()
