from __future__ import annotations
from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────────────────────
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    secret_key: SecretStr = Field(default="dev-secret-change-in-production")

    # ── Database ───────────────────────────────────────────────────────────────
    database_url: str = (
        "postgresql+psycopg://investing:investing@localhost:5432/investing_agent"
    )

    # ── Zerodha ────────────────────────────────────────────────────────────────
    zerodha_mcp_url: str = "https://mcp.kite.trade/mcp"
    zerodha_api_key: SecretStr | None = None
    zerodha_api_secret: SecretStr | None = None
    zerodha_access_token: SecretStr | None = None

    # ── LLM ────────────────────────────────────────────────────────────────────
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-4-6"

    # ── Broker safety ─────────────────────────────────────────────────────────
    # Must remain false until human-approval workflow is fully validated
    broker_execution_enabled: bool = False

    # ── LangSmith ─────────────────────────────────────────────────────────────
    langchain_tracing_v2: bool = False
    langchain_api_key: SecretStr | None = None
    langchain_project: str = "investing-agent"

    # ── Data freshness (seconds) ──────────────────────────────────────────────
    price_staleness_seconds: int = 300
    portfolio_staleness_seconds: int = 3600
    fundamentals_staleness_seconds: int = 86400

    # ── Default user ──────────────────────────────────────────────────────────
    default_user_id: str = "default"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def sync_database_url(self) -> str:
        """Sync URL for Alembic (psycopg3 works for both)."""
        return self.database_url.replace("+psycopg", "+psycopg")

    @property
    def async_database_url(self) -> str:
        """Async URL for SQLAlchemy async engine."""
        url = self.database_url
        if "+psycopg" in url and "_async" not in url:
            # psycopg3 supports async with the same dialect name
            return url
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
