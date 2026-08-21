from __future__ import annotations

"""Unit tests for the broker-history importer registry and domain schemas
(no database, no I/O) -- provider-neutral wiring only."""

from pathlib import Path

import pytest

from investing_agent.schemas.broker_history import BROKER_STRATEGY_PROFILE
from investing_agent.services.ingestion.broker_history import get_importer
from investing_agent.services.ingestion.broker_history.indmoney import (
    INDmoneyStatementImporter,
)
from investing_agent.services.ingestion.broker_history.zerodha import (
    ZerodhaStatementImporter,
)


class TestBrokerStrategyProfileMapping:
    def test_zerodha_maps_to_long_term(self) -> None:
        assert BROKER_STRATEGY_PROFILE["ZERODHA"] == "LONG_TERM"

    def test_indmoney_maps_to_medium_term(self) -> None:
        assert BROKER_STRATEGY_PROFILE["INDMONEY"] == "MEDIUM_TERM"


class TestImporterRegistry:
    def test_get_importer_returns_registered_indmoney_importer(self) -> None:
        importer = get_importer("INDMONEY")
        assert isinstance(importer, INDmoneyStatementImporter)

    def test_get_importer_returns_registered_zerodha_importer(self) -> None:
        importer = get_importer("ZERODHA")
        assert isinstance(importer, ZerodhaStatementImporter)


class TestINDmoneyStatementImporterStub:
    def test_parse_raises_not_implemented(self) -> None:
        importer = INDmoneyStatementImporter()
        with pytest.raises(NotImplementedError):
            importer.parse(Path("/tmp/does-not-matter.csv"))
