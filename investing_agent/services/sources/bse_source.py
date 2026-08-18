from __future__ import annotations

"""BSEDataSource — production adapter, Phase 3A.

Bake-off finding (see REPORT.md): api.bseindia.com/BseIndiaAPI/api/* did not
return usable data to a plain GET even with a Referer header — it redirected
to an error page. Guessing further undocumented parameters would cross into
reverse-engineering a private API contract, which is explicitly out of
scope. This adapter therefore returns empty results honestly rather than
fabricate working scraping logic that was never verified — consistent with
"report unresolved source issues instead of guessing."

Its role in Phase 3A is cross-check only: when BSE access is later restored
(official channel, or a licensed vendor), NSEDataSource-derived corporate
actions can be reconciled against BSE's independently filed record for the
same event via services/verification.py's cross_source method. Until then,
verification_status stays "unverified" for anything that would have relied
on BSE confirmation.
"""


import httpx
import structlog

from investing_agent.services.sources.interfaces import (
    CorporateActionSource,
    DiscoveredDocument,
    RawCorporateAction,
)

log = structlog.get_logger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; investing-agent/1.0; +research use)",
    "Referer": "https://www.bseindia.com/",
}


class BSEDataSource(CorporateActionSource):
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(headers=_HEADERS, timeout=15.0, follow_redirects=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_dividends(
        self, symbol: str
    ) -> tuple[list[RawCorporateAction], DiscoveredDocument | None]:
        actions, doc = await self.get_corporate_actions(symbol)
        return [a for a in actions if a.action_type == "dividend"], doc

    async def get_board_meetings(
        self, symbol: str
    ) -> tuple[list[RawCorporateAction], DiscoveredDocument | None]:
        return [], None

    async def get_corporate_actions(
        self, symbol: str
    ) -> tuple[list[RawCorporateAction], DiscoveredDocument | None]:
        """Documented negative result — see module docstring."""
        log.warning(
            "bse_source.no_verified_access",
            symbol=symbol,
            reason="api.bseindia.com endpoints not usable per bake-off; not pursued further",
        )
        return [], None
