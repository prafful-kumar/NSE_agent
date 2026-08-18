from __future__ import annotations
"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from investing_agent.app.api import agent, calendar, company, portfolio, watchlist
from investing_agent.config.logging import configure_logging
from investing_agent.config.settings import get_settings

settings = get_settings()
configure_logging(
    log_level=settings.log_level,
    json_output=not settings.is_development,
)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    # Startup: nothing in Phase 1 (DB handled by Alembic migrations)
    yield
    # Shutdown: close connection pool
    from investing_agent.db.session import engine
    await engine.dispose()


app = FastAPI(
    title="Investing Agent API",
    description="Long-term Indian equity investment research agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.is_development else [],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(portfolio.router)
app.include_router(watchlist.router)
app.include_router(company.router)
app.include_router(calendar.router)
app.include_router(agent.router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": "0.1.0",
        "env": settings.app_env,
        "broker_execution_enabled": settings.broker_execution_enabled,
    }
