from __future__ import annotations

"""FastAPI dependency injection — Phase 2.

PortfolioReader is injected into portfolio-related routes.
BrokerExecutor is intentionally NOT injected here — it is only used in the
human-approval node (Phase 8) after explicit user confirmation.
"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.config.settings import Settings, get_settings
from investing_agent.db.session import AsyncSessionLocal
from investing_agent.gateway.portfolio_reader import PortfolioReader


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_portfolio_reader(
    settings: Annotated[Settings, Depends(get_settings)],
) -> PortfolioReader:
    """Return the configured PortfolioReader (read-only, never executes trades).

    Uses ZerodhaKiteMCPReader when ZERODHA_ACCESS_TOKEN is set;
    falls back to MockPortfolioReader otherwise.
    """
    if settings.zerodha_access_token:
        from investing_agent.gateway.zerodha_reader import ZerodhaKiteMCPReader
        return ZerodhaKiteMCPReader(settings)

    from investing_agent.gateway.mock_reader import MockPortfolioReader
    return MockPortfolioReader(scenario="normal")


# ── Type aliases ───────────────────────────────────────────────────────────────

DbSession = Annotated[AsyncSession, Depends(get_db)]
PortfolioReaderDep = Annotated[PortfolioReader, Depends(get_portfolio_reader)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

# Keep BrokerDep as an alias pointing to PortfolioReader for backward compat
# with any existing routes that imported BrokerDep before Phase 2.
BrokerDep = PortfolioReaderDep
