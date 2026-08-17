from __future__ import annotations

"""Portfolio API routes — Phase 2.

GET  /portfolio          → latest snapshot from DB
POST /portfolio/sync     → trigger full sync pipeline via PortfolioSyncService
GET  /portfolio/history  → list of past snapshots
"""

from fastapi import APIRouter, HTTPException

from investing_agent.app.dependencies import DbSession, PortfolioReaderDep, SettingsDep
from investing_agent.db.repositories.portfolio import PortfolioRepository
from investing_agent.schemas.portfolio import PortfolioSnapshotRead
from investing_agent.schemas.sync import SyncResult

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("", response_model=PortfolioSnapshotRead)
async def get_portfolio(
    settings: SettingsDep,
    db: DbSession,
) -> PortfolioSnapshotRead:
    """Return the latest portfolio snapshot from the database.

    Does NOT hit the broker.  Call POST /portfolio/sync to refresh.
    """
    repo = PortfolioRepository(db)
    snapshot = await repo.get_latest(settings.default_user_id)
    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail=(
                "No portfolio snapshot found. "
                "Call POST /portfolio/sync to fetch from Zerodha."
            ),
        )
    return PortfolioSnapshotRead.model_validate(snapshot)


@router.post("/sync", response_model=SyncResult)
async def sync_portfolio(
    settings: SettingsDep,
    db: DbSession,
    reader: PortfolioReaderDep,
) -> SyncResult:
    """Fetch the latest portfolio from Zerodha and persist a snapshot.

    Returns the full SyncResult including reconciliation warnings.
    Requires ZERODHA_ACCESS_TOKEN to be set (otherwise uses mock reader).
    """
    from investing_agent.gateway.portfolio_reader import BrokerAuthError, BrokerUnavailableError
    from investing_agent.services.portfolio_sync import PortfolioSyncService

    svc = PortfolioSyncService(reader=reader, session=db)
    try:
        result = await svc.sync(settings.default_user_id)
    except BrokerAuthError as exc:
        raise HTTPException(
            status_code=401,
            detail=f"Zerodha authentication failed: {exc}. Regenerate your access token.",
        ) from exc
    except BrokerUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Zerodha MCP unavailable: {exc}. Retry later.",
        ) from exc

    return result


@router.get("/history", response_model=list[PortfolioSnapshotRead])
async def portfolio_history(
    settings: SettingsDep,
    db: DbSession,
    limit: int = 30,
) -> list[PortfolioSnapshotRead]:
    """Return recent portfolio snapshots ordered by date descending."""
    repo = PortfolioRepository(db)
    snapshots = await repo.get_history(settings.default_user_id, limit=limit)
    return [PortfolioSnapshotRead.model_validate(s) for s in snapshots]
