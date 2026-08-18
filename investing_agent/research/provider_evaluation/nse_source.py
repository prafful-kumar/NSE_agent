from __future__ import annotations

"""NSE evaluation adapter — bake-off only, NOT the Phase 3A production adapter.

Findings this adapter exists to document (see REPORT.md for full writeup):

- www.nseindia.com/api/corporates-corporateActions and
  www.nseindia.com/api/results-comparision respond 200 with real JSON to a
  single plain HTTPS GET, no cookies/session/login required. Verified live
  for BEL and HAL on 2026-08-17 (see evidence/*.json).
- These are the same JSON endpoints NSE's own website widgets call. They are
  NOT documented as a public API, NOT versioned, and every response carries
  Akamai Bot Manager cookies (_abck, bm_sz) — a sign NSE can start
  challenging automated traffic at any time without notice.
- Treat this adapter as a best-effort, low-volume, rate-limited SUPPLEMENTARY
  source only. It must never be the sole system of record for Tier-1 facts;
  the archived NSE circular/filing PDF or XBRL document is the record of
  truth. See REPORT.md "Point-in-time integrity" section.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from investing_agent.research.provider_evaluation.interfaces import (
    CorporateActionSource,
    Evidence,
    FinancialResultSource,
    RawCorporateAction,
    RawFinancialResult,
)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; investing-agent-research/1.0)"}
_MIN_REQUEST_GAP_SECONDS = 1.5  # self-imposed rate limit; be a polite citizen


class NSEEvaluationSource(CorporateActionSource, FinancialResultSource):
    def __init__(self, evidence_dir: Path | None = None) -> None:
        self._client = httpx.AsyncClient(headers=_HEADERS, timeout=15.0)
        self._evidence_dir = evidence_dir
        self._last_request_at: float = 0.0
        self.evidence: list[Evidence] = []

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str, request_type: str, symbol: str) -> tuple[int, bytes]:
        elapsed = asyncio.get_event_loop().time() - self._last_request_at
        if elapsed < _MIN_REQUEST_GAP_SECONDS:
            await asyncio.sleep(_MIN_REQUEST_GAP_SECONDS - elapsed)
        resp = await self._client.get(url)
        self._last_request_at = asyncio.get_event_loop().time()

        ev = Evidence(
            provider="NSE",
            request_type=request_type,
            symbol=symbol,
            requested_at=datetime.now(UTC),
            source_url=url,
            http_status=resp.status_code,
            response_hash=Evidence.hash_body(resp.content) if resp.content else None,
        )
        self.evidence.append(ev)
        if self._evidence_dir:
            self._write_evidence(ev, resp.content)
        return resp.status_code, resp.content

    def _write_evidence(self, ev: Evidence, body: bytes) -> None:
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        stamp = ev.requested_at.strftime("%Y%m%dT%H%M%SZ")
        path = self._evidence_dir / f"nse_{ev.request_type}_{ev.symbol}_{stamp}.json"
        path.write_bytes(body)

    async def get_dividends(self, symbol: str) -> list[RawCorporateAction]:
        return [a for a in await self.get_corporate_actions(symbol) if a.action_type == "dividend"]

    async def get_board_meetings(self, symbol: str) -> list[RawCorporateAction]:
        return []  # NSE exposes board meetings via a separate undocumented endpoint;
        # not exercised in this bake-off — see REPORT.md open questions.

    async def get_corporate_actions(self, symbol: str) -> list[RawCorporateAction]:
        import json

        url = f"https://www.nseindia.com/api/corporates-corporateActions?index=equities&symbol={symbol}"
        status, body = await self._get(url, "corporate_actions", symbol)
        now = datetime.now(UTC)
        if status != 200 or not body:
            return []
        rows: list[dict[str, Any]] = json.loads(body)
        out: list[RawCorporateAction] = []
        for row in rows:
            subject = (row.get("subject") or "").lower()
            action_type = (
                "dividend" if "dividend" in subject
                else "bonus" if "bonus" in subject
                else "split" if "split" in subject or "sub-division" in subject
                else "buyback" if "buyback" in subject
                else "other"
            )
            out.append(
                RawCorporateAction(
                    provider="NSE",
                    symbol=row.get("symbol", symbol),
                    isin=row.get("isin"),
                    action_type=action_type,
                    announced_date=row.get("caBroadcastDate"),
                    ex_date=row.get("exDate"),
                    record_date=row.get("recDate"),
                    payment_date=None,  # NSE does not publish payment_date on this endpoint
                    amount_text=row.get("subject"),
                    published_at=None,  # caBroadcastDate is often null; not reliably parseable
                    available_at=now,
                    source_url=url,
                    raw=row,
                )
            )
        return out

    async def get_quarterly_results(self, symbol: str) -> list[RawFinancialResult]:
        import json

        url = f"https://www.nseindia.com/api/results-comparision?index=equities&symbol={symbol}"
        status, body = await self._get(url, "quarterly_results", symbol)
        now = datetime.now(UTC)
        if status != 200 or not body:
            return []
        payload = json.loads(body)
        rows: list[dict[str, Any]] = payload.get("resCmpData", [])
        out: list[RawFinancialResult] = []
        for row in rows:
            filed_at = None
            create_dt = row.get("re_create_dt")
            if create_dt:
                try:
                    filed_at = datetime.strptime(create_dt, "%d-%b-%Y").replace(tzinfo=UTC)
                except ValueError:
                    filed_at = None
            out.append(
                RawFinancialResult(
                    provider="NSE",
                    symbol=symbol,
                    period_label=f"{row.get('re_from_dt', '')}..{row.get('re_to_dt', '')}",
                    is_consolidated=None,  # not explicit on this endpoint; see REPORT.md
                    revenue=row.get("re_total_inc") or row.get("re_net_sale"),
                    pat=row.get("re_con_pro_loss"),
                    eps_basic=row.get("re_basic_eps_for_cont_dic_opr"),
                    result_date=create_dt,
                    filed_at=filed_at,
                    available_at=now,
                    # XBRL-derived tags (re_* names), not raw XBRL
                    extraction_method="structured_api",
                    source_url=url,
                    raw=row,
                )
            )
        return out

    async def get_annual_results(self, symbol: str) -> list[RawFinancialResult]:
        return []  # not exercised in this bake-off; annual results live on the
        # corporate-filings-financial-results HTML page, which is JS-rendered
        # and was not scraped (see REPORT.md).
