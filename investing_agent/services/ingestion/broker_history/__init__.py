from __future__ import annotations

"""Broker-account historical import (Phase 6A).

Importing this package registers every known StatementImporter subclass
(via the @register_importer decorator in each broker module) into
base._REGISTRY, so get_importer() works without the caller needing to know
which submodules exist.
"""

from investing_agent.services.ingestion.broker_history import indmoney  # noqa: F401
from investing_agent.services.ingestion.broker_history import zerodha  # noqa: F401
from investing_agent.services.ingestion.broker_history.base import (
    ParsedCashFlow,
    ParsedDividend,
    ParsedStatement,
    ParsedTrade,
    StatementImporter,
    get_importer,
    register_importer,
)

__all__ = [
    "ParsedCashFlow",
    "ParsedDividend",
    "ParsedStatement",
    "ParsedTrade",
    "StatementImporter",
    "get_importer",
    "register_importer",
]
