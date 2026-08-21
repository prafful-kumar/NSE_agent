from __future__ import annotations

"""INDmoney statement importer -- interface stub only.

Per the user's explicit instruction: do NOT build the INDmoney parser
until a real exported statement/tax-P&L file has been obtained and its
columns inspected (mirroring the discipline used for Zerodha). This class
exists only so the registry and CLI have a stable target to wire against;
parse() intentionally raises until that inspection happens.
"""

from pathlib import Path
from typing import ClassVar

from investing_agent.schemas.broker_history import Broker
from investing_agent.services.ingestion.broker_history.base import (
    ParsedStatement,
    StatementImporter,
    register_importer,
)


@register_importer
class INDmoneyStatementImporter(StatementImporter):
    broker: ClassVar[Broker] = "INDMONEY"

    def parse(self, file_path: Path) -> ParsedStatement:
        raise NotImplementedError(
            "INDmoneyStatementImporter.parse() is not yet implemented -- "
            "an INDmoney statement/tax-P&L export has not been inspected. "
            "Obtain a real export and inspect its columns before implementing."
        )
