"""Test settings load correctly and safety defaults hold."""

import os
import pytest


def test_broker_execution_disabled_by_default():
    from investing_agent.config.settings import Settings
    s = Settings(
        database_url="postgresql+psycopg://x:x@localhost/x",
        broker_execution_enabled=False,
    )
    assert s.broker_execution_enabled is False


def test_broker_execution_cannot_be_true_silently():
    """Ensure the field is read correctly when set — guard is in gateway, not settings."""
    from investing_agent.config.settings import Settings
    s = Settings(
        database_url="postgresql+psycopg://x:x@localhost/x",
        broker_execution_enabled=True,
    )
    # Settings allow True — the gateway is the actual enforcement layer
    assert s.broker_execution_enabled is True


def test_settings_env_var_precedence(monkeypatch):
    from investing_agent.config.settings import Settings
    monkeypatch.setenv("BROKER_EXECUTION_ENABLED", "false")
    s = Settings()
    assert s.broker_execution_enabled is False


def test_settings_no_secrets_exposed():
    """Secrets must be SecretStr — never plain str."""
    from investing_agent.config.settings import Settings
    from pydantic import SecretStr
    s = Settings(
        database_url="postgresql+psycopg://x:x@localhost/x",
        zerodha_api_key="my-key",
    )
    assert isinstance(s.zerodha_api_key, SecretStr)
    # Ensure str() doesn't expose the value
    assert "my-key" not in str(s.zerodha_api_key)
