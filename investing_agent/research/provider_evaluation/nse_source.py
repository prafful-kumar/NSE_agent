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
    FilingSource,
    FinancialResultSource,
    RawCorporateAction,
    RawFiling,
    RawFinancialResult,
)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; investing-agent-research/1.0)"}
_MIN_REQUEST_GAP_SECONDS = 1.5  # self-imposed rate limit; be a polite citizen

# Candidate categories for the corporate-announcements endpoint — NSE's
# announcement feed is the one plausible free path to investor
# presentations, annual reports, and concall transcripts (all typically
# disclosed as exchange announcements, not separate document types). Not
# verified working as of this bake-off extension — that's exactly what
# this module exists to find out. See REPORT.md §11.
_ANNOUNCEMENTS_URL = "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}"
_ANNUAL_REPORTS_URL = "https://www.nseindia.com/api/corp-info?symbol={symbol}&corpType=annualreport&market=equities"


class NSEEvaluationSource(CorporateActionSource, FinancialResultSource, FilingSource):
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

    # ── Phase 3B addendum: document discovery ───────────────────────────────
    # Unlike get_corporate_actions/get_quarterly_results (proven working in
    # the original bake-off), these four probe *candidate* endpoints that
    # have not been live-verified. Failure (non-200, empty body, or a body
    # that isn't the expected JSON shape) is reported as an empty list with
    # evidence logged, never guessed around. See REPORT.md §11.

    async def get_announcements(self, symbol: str) -> list[RawFiling]:
        import json

        url = _ANNOUNCEMENTS_URL.format(symbol=symbol)
        status, body = await self._get(url, "announcements", symbol)
        now = datetime.now(UTC)
        if status != 200 or not body:
            return []
        try:
            rows: list[dict[str, Any]] = json.loads(body)
        except json.JSONDecodeError:
            return []  # got a 200 but not JSON — likely a challenge/HTML page
        out: list[RawFiling] = []
        for row in rows:
            attachment = row.get("attchmntFile")
            out.append(
                RawFiling(
                    provider="NSE",
                    symbol=symbol,
                    filing_type=_classify_announcement(row.get("desc") or row.get("subject")),
                    title=row.get("desc") or row.get("subject") or "(untitled)",
                    filing_date=row.get("an_dt") or row.get("sort_date"),
                    document_url=attachment,
                    available_at=now,
                    source_url=url,
                    raw=row,
                )
            )
        return out

    async def get_filings(self, symbol: str) -> list[RawFiling]:
        return await self.get_announcements(symbol)

    async def get_annual_reports(self, symbol: str) -> list[RawFiling]:
        import json

        url = _ANNUAL_REPORTS_URL.format(symbol=symbol)
        status, body = await self._get(url, "annual_reports", symbol)
        now = datetime.now(UTC)
        if status != 200 or not body:
            return []
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return []
        rows: list[dict[str, Any]] = (
            payload if isinstance(payload, list) else payload.get("data", [])
        )
        out: list[RawFiling] = []
        for row in rows:
            out.append(
                RawFiling(
                    provider="NSE",
                    symbol=symbol,
                    filing_type="annual_report",
                    title=row.get("fromYr") and f"Annual Report {row.get('fromYr')}-{row.get('toYr')}"
                    or "Annual Report",
                    filing_date=row.get("toYr"),
                    document_url=row.get("Attachment") or row.get("attachment"),
                    available_at=now,
                    source_url=url,
                    raw=row,
                )
            )
        return out

    async def get_investor_presentations(self, symbol: str) -> list[RawFiling]:
        """Investor presentations are not a distinct NSE endpoint — they show
        up (if at all) as a category within the announcements feed, so this
        just filters get_announcements(). Confirm in evidence/ whether any
        rows actually classify as investor_presentation."""
        return [f for f in await self.get_announcements(symbol) if f.filing_type == "investor_presentation"]

    async def check_pdf_downloadable(self, document_url: str) -> tuple[bool, int | None, str | None]:
        """Fetches the first bytes of a candidate attachment URL and checks
        for the %PDF magic number, without saving the full binary as
        evidence (keep the repo small). Returns (is_pdf, size_bytes,
        content_type)."""
        try:
            resp = await self._client.get(document_url)
        except httpx.HTTPError as exc:
            return False, None, str(exc)
        is_pdf = resp.content[:4] == b"%PDF"
        return is_pdf, len(resp.content), resp.headers.get("content-type")


def _classify_announcement(text: str | None) -> str:
    """Best-effort categorization from the announcement description/subject
    text. Never guesses "other" into something more specific than the text
    actually supports."""
    t = (text or "").lower()
    if "investor presentation" in t or "investor  presentation" in t:
        return "investor_presentation"
    if "annual report" in t:
        return "annual_report"
    if "transcript" in t or "con call" in t or "concall" in t or "earnings call" in t:
        return "concall_transcript"
    if "financial result" in t or "quarterly result" in t:
        return "quarterly_result"
    return "announcement"
