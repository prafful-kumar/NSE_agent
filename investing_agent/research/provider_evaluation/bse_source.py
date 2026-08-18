from __future__ import annotations

"""BSE evaluation adapter — bake-off only.

Finding: unlike NSE, BSE's api.bseindia.com/BseIndiaAPI/api/* endpoints did
NOT return usable data to a plain GET (with or without a Referer header) —
verified live on 2026-08-17: requests to CorpactCurrent redirected (302) to
api.bseindia.com/error_Bse.html. Guessing further undocumented parameter
combinations to "make it work" would cross into reverse-engineering a
private API, which is explicitly out of scope for this harness.

Conclusion encoded here: BSE is NOT usable today as a live structured JSON
source without solving that API contract properly (e.g. via BSE's own
developer/member channels) or paying a licensed vendor. For Phase 3A, BSE's
role is: (a) cross-check counterpart for NSE-sourced corporate actions using
the officially published, human-readable announcement/PDF pages, and
(b) an archival source for the exchange-stamped filing PDF itself. Both are
document-fetch operations (see REPORT.md), not this adapter's job — this
class exists only to record the negative result honestly rather than skip
BSE entirely.
"""

from datetime import UTC, datetime
from pathlib import Path

import httpx

from investing_agent.research.provider_evaluation.interfaces import (
    CorporateActionSource,
    Evidence,
    RawCorporateAction,
)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; investing-agent-research/1.0)",
    "Referer": "https://www.bseindia.com/",
}


class BSEEvaluationSource(CorporateActionSource):
    def __init__(self, evidence_dir: Path | None = None) -> None:
        self._client = httpx.AsyncClient(headers=_HEADERS, timeout=15.0, follow_redirects=True)
        self._evidence_dir = evidence_dir
        self.evidence: list[Evidence] = []

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_dividends(self, symbol: str) -> list[RawCorporateAction]:
        return [a for a in await self.get_corporate_actions(symbol) if a.action_type == "dividend"]

    async def get_board_meetings(self, symbol: str) -> list[RawCorporateAction]:
        return []

    async def get_corporate_actions(self, symbol: str) -> list[RawCorporateAction]:
        """Documents the negative result — see module docstring. scripcode
        lookup is itself a BSE-specific identifier we don't maintain, so this
        was tested against BEL's known scripcode (500049) only.
        """
        url = "https://api.bseindia.com/BseIndiaAPI/api/CorpactCurrent/w?scripcode=500049&Fyear=&Segment=0"
        resp = await self._client.get(url)
        ev = Evidence(
            provider="BSE",
            request_type="corporate_actions",
            symbol=symbol,
            requested_at=datetime.now(UTC),
            source_url=url,
            http_status=resp.status_code,
            response_hash=Evidence.hash_body(resp.content) if resp.content else None,
            notes="Redirected to api.bseindia.com/error_Bse.html — endpoint contract not "
                  "publicly documented; not pursued further per bake-off scope constraints.",
        )
        self.evidence.append(ev)
        if self._evidence_dir:
            self._evidence_dir.mkdir(parents=True, exist_ok=True)
            stamp = ev.requested_at.strftime("%Y%m%dT%H%M%SZ")
            (self._evidence_dir / f"bse_corporate_actions_{symbol}_{stamp}.txt").write_text(
                f"HTTP {resp.status_code}\nfinal_url={resp.url}\n\n{resp.text[:2000]}"
            )
        return []
