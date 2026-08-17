from __future__ import annotations
"""FastAPI dependency injection."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.config.settings import Settings, get_settings
from investing_agent.db.session import AsyncSessionLocal
from investing_agent.gateway.base import BrokerGateway
from investing_agent.gateway.mock import MockBrokerGateway


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_broker(settings: Annotated[Settings, Depends(get_settings)]) -> BrokerGateway:
    """Return the appropriate broker gateway.

    In Phase 1 we always return MockBrokerGateway.
    Phase 2 will conditionally return ZerodhaKiteMCPGateway when credentials are set.
    """
    return MockBrokerGateway(execution_enabled=settings.broker_execution_enabled)


# Type aliases for annotated dependencies
DbSession = Annotated[AsyncSession, Depends(get_db)]
BrokerDep = Annotated[BrokerGateway, Depends(get_broker)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
