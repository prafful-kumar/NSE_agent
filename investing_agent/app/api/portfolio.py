from __future__ import annotations
"""Portfolio API routes."""

from fastapi import APIRouter, HTTPException

from investing_agent.app.dependencies import BrokerDep, DbSession, SettingsDep
from investing_agent.db.repositories.portfolio import PortfolioRepository
from investing_agent.schemas.portfolio import PortfolioSnapshotRead

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("", response_model=PortfolioSnapshotRead)
async def get_portfolio(
    settings: SettingsDep,
    db: DbSession,
) -> PortfolioSnapshotRead:
    """Return the latest portfolio snapshot from the database."""
    repo = PortfolioRepository(db)
    snapshot = await repo.get_latest(settings.default_user_id)
    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail="No portfolio snapshot found. Call POST /portfolio/sync first.",
        )
    return PortfolioSnapshotRead.model_validate(snapshot)


@router.post("/sync", response_model=PortfolioSnapshotRead)
async def sync_portfolio(
    settings: SettingsDep,
    db: DbSession,
    broker: BrokerDep,
) -> PortfolioSnapshotRead:
    """Fetch latest portfolio from broker and persist it."""
    response = await broker.get_holdings()
    repo = PortfolioRepository(db)
    snapshot = await repo.save_from_broker(settings.default_user_id, response)
    return PortfolioSnapshotRead.model_validate(snapshot)
