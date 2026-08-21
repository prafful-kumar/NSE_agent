from __future__ import annotations

"""NSEDataSource — production adapter, Phase 3A.

Bake-off findings this adapter encodes (see
investing_agent/research/provider_evaluation/REPORT.md for the full writeup):

- www.nseindia.com/api/corporates-corporateActions and
  .../api/results-comparision respond 200 with real JSON to a plain HTTPS
  GET, no auth. Verified live for BEL and HAL on 2026-08-17.
- These are undocumented, unversioned convenience endpoints, NOT NSE's
  archived filing PDF/XBRL. Every DiscoveredDocument this adapter returns is
  therefore tagged source_type="nse_json_hint" (never "nse_filing_pdf" /
  "nse_filing_xbrl") — the verification service refuses to mark facts
  derived from a "*_hint" source_type as verified without a second,
  independent confirmation (see services/verification.py).
- statement_scope_hint is hard-coded to "STANDALONE", not inferred from the
  response field names: the field `re_con_pro_loss` ("consolidated
  profit/loss") was empirically found to hold BEL's STANDALONE PAT
  (₹1,316.06 Cr matched BEL's own press release exactly). Trusting the field
  name would have silently mislabeled the data — see
  tests/unit/test_financial_normalization.py for the regression test.
- unit_scale_hint is hard-coded to "LAKH": re_net_sale=575612 matched BEL's
  reported ₹5,756.12 Cr revenue from operations exactly when divided by 100
  (575612 / 100 = 5756.12).
- NSE never populates a payment_date on the corporate-actions endpoint —
  the field literally isn't in the schema. payment_date always stays None
  from this adapter; it must come from a real filing document.
- This adapter self-rate-limits (1.5s between requests) and is intended for
  low-volume company-level discovery, not bulk scraping.

Phase 3B addendum (2026-08-18): FilingSource IS now implemented, backed by
the same announcements JSON feed the bake-off confirmed live for BEL/HAL
(1025 rows for BEL, real `attchmntFile` PDF/ZIP links, verified downloadable
— see REPORT.md §11). Resilience: transient failures (network errors,
429/5xx) are retried with exponential backoff via tenacity; an HTTP 403
(Akamai Bot Manager block) is NEVER retried — it's raised as
SourceAccessError so callers can degrade gracefully instead of hammering a
block. No anti-bot bypass (no cookie/session bootstrap, no browser
automation, no header spoofing beyond a plain descriptive User-Agent) is
attempted anywhere in this module.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from investing_agent.services.sources.interfaces import (
    CorporateActionSource,
    DiscoveredDocument,
    DownloadedAttachment,
    FilingSource,
    FinancialResultSource,
    RawAnnouncement,
    RawCorporateAction,
    RawFinancialResult,
    SourceAccessError,
    SourceTransientError,
)

log = structlog.get_logger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; investing-agent/1.0; +research use)"}
_MIN_REQUEST_GAP_SECONDS = 1.5
# "A" = audited, "U" = unaudited/limited-review; anything else is left
# unresolved rather than guessed.
_RES_TYPE_AUDITED = {"A": True, "U": False}

_ANNOUNCEMENTS_URL = (
    "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}"
)
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
# Module-level so tests can monkeypatch these to near-zero and keep retry
# tests fast — read fresh on every call via AsyncRetrying, not baked into a
# decorator at import time.
_RETRY_ATTEMPTS = 3
_RETRY_WAIT_MIN_SECONDS = 2.0
_RETRY_WAIT_MAX_SECONDS = 10.0


class NSEDataSource(CorporateActionSource, FinancialResultSource, FilingSource):
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(headers=_HEADERS, timeout=15.0)
        self._last_request_at: float = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_with_headers(self, url: str) -> tuple[int, bytes, httpx.Headers]:
        elapsed = asyncio.get_event_loop().time() - self._last_request_at
        if elapsed < _MIN_REQUEST_GAP_SECONDS:
            await asyncio.sleep(_MIN_REQUEST_GAP_SECONDS - elapsed)
        try:
            resp = await self._client.get(url)
        except httpx.TransportError as exc:
            self._last_request_at = asyncio.get_event_loop().time()
            raise SourceTransientError(f"network error fetching {url}: {exc}") from exc
        self._last_request_at = asyncio.get_event_loop().time()
        log.info("nse_source.request", url=url, status=resp.status_code)
        return resp.status_code, resp.content, resp.headers

    async def _get(self, url: str) -> tuple[int, bytes]:
        status, body, _ = await self._get_with_headers(url)
        return status, body

    async def _get_resilient(self, url: str) -> tuple[int, bytes, httpx.Headers]:
        """Like _get_with_headers, but retries transient failures
        (SourceTransientError) with exponential backoff, and raises
        SourceAccessError immediately (no retry) on HTTP 403."""
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(SourceTransientError),
            stop=stop_after_attempt(_RETRY_ATTEMPTS),
            wait=wait_exponential(
                multiplier=1, min=_RETRY_WAIT_MIN_SECONDS, max=_RETRY_WAIT_MAX_SECONDS
            ),
            reraise=True,
        ):
            with attempt:
                status, body, headers = await self._get_with_headers(url)
                if status == 403:
                    raise SourceAccessError(
                        f"NSE returned 403 for {url} (likely anti-bot block) — not retrying"
                    )
                if status in _RETRYABLE_STATUSES:
                    raise SourceTransientError(f"NSE returned {status} for {url}")
                return status, body, headers
        raise AssertionError("unreachable — AsyncRetrying always returns or raises")

    async def get_dividends(
        self, symbol: str
    ) -> tuple[list[RawCorporateAction], DiscoveredDocument | None]:
        actions, doc = await self.get_corporate_actions(symbol)
        return [a for a in actions if a.action_type == "dividend"], doc

    async def get_board_meetings(
        self, symbol: str
    ) -> tuple[list[RawCorporateAction], DiscoveredDocument | None]:
        return [], None  # not exercised — see class docstring

    async def get_corporate_actions(
        self, symbol: str
    ) -> tuple[list[RawCorporateAction], DiscoveredDocument | None]:
        url = f"https://www.nseindia.com/api/corporates-corporateActions?index=equities&symbol={symbol}"
        status, body = await self._get(url)
        now = datetime.now(UTC)
        if status != 200 or not body:
            return [], None

        doc = DiscoveredDocument(
            company_symbol=symbol,
            exchange="NSE",
            source="NSE",
            source_type="nse_json_hint",
            filing_type="announcement",
            document_type="json",
            title=f"NSE corporate actions hint — {symbol}",
            content=body,
            source_url=url,
            fetched_at=now,
        )

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
            dividend_type = (
                "interim" if "interim" in subject
                else "special" if "special" in subject
                else None  # NOT defaulted to "final" — genuinely ambiguous otherwise
            )
            out.append(
                RawCorporateAction(
                    action_type=action_type,
                    announced_date=row.get("caBroadcastDate"),
                    event_date=row.get("exDate"),
                    ex_date=row.get("exDate"),
                    record_date=row.get("recDate"),
                    payment_date=None,  # never populated by this endpoint
                    agm_date=None,
                    amount_text=row.get("subject"),
                    dividend_type=dividend_type,
                    board_meeting_announced_at=None,
                    board_meeting_date=None,
                    expected_result_date=None,
                    actual_result_published_at=None,
                    raw=row,
                )
            )
        return out, doc

    async def get_quarterly_results(
        self, symbol: str
    ) -> tuple[list[RawFinancialResult], DiscoveredDocument | None]:
        url = f"https://www.nseindia.com/api/results-comparision?index=equities&symbol={symbol}"
        status, body = await self._get(url)
        now = datetime.now(UTC)
        if status != 200 or not body:
            return [], None

        doc = DiscoveredDocument(
            company_symbol=symbol,
            exchange="NSE",
            source="NSE",
            source_type="nse_json_hint",
            filing_type="quarterly_result",
            document_type="json",
            title=f"NSE quarterly results hint — {symbol}",
            content=body,
            source_url=url,
            fetched_at=now,
        )

        payload = json.loads(body)
        rows: list[dict[str, Any]] = payload.get("resCmpData", [])
        out: list[RawFinancialResult] = []
        for row in rows:
            out.append(
                RawFinancialResult(
                    period_start=row.get("re_from_dt"),
                    period_end=row.get("re_to_dt"),
                    result_date=row.get("re_create_dt"),
                    revenue=row.get("re_total_inc") or row.get("re_net_sale"),
                    pat=row.get("re_con_pro_loss"),
                    pbt=row.get("re_pro_loss_bef_tax"),
                    eps_basic=row.get("re_basic_eps_for_cont_dic_opr"),
                    is_audited_hint=_RES_TYPE_AUDITED.get(row.get("re_res_type", "")),
                    # Hard-coded from verified evidence, NOT from field names
                    # (re_con_pro_loss reads "consolidated" but holds
                    # standalone PAT) — see module docstring.
                    statement_scope_hint="STANDALONE",
                    unit_scale_hint="LAKH",
                    extraction_method="structured_api",
                    raw=row,
                )
            )
        return out, doc

    async def get_annual_results(
        self, symbol: str
    ) -> tuple[list[RawFinancialResult], DiscoveredDocument | None]:
        return [], None  # not exercised — see class docstring

    # ── FilingSource (Phase 3B) ──────────────────────────────────────────────

    async def get_announcements(self, symbol: str) -> list[RawAnnouncement]:
        """Raises SourceAccessError (403/blocked) or SourceTransientError
        (retries exhausted) — the caller (FilingIngestionService) decides
        how to degrade; this method never swallows those two."""
        url = _ANNOUNCEMENTS_URL.format(symbol=symbol)
        status, body, _ = await self._get_resilient(url)
        if status != 200 or not body:
            log.warning("nse_source.announcements_empty", symbol=symbol, status=status)
            return []
        try:
            rows: list[dict[str, Any]] = json.loads(body)
        except json.JSONDecodeError:
            # A 200 with a non-JSON body (HTML challenge/error page) is
            # reported as "no data", never guessed around.
            log.warning("nse_source.announcements_malformed", symbol=symbol, url=url)
            return []

        out: list[RawAnnouncement] = []
        for row in rows:
            desc = row.get("desc") or row.get("subject")
            filing_date = row.get("an_dt") or row.get("sort_date")
            # NSE uses the literal string "-" as attchmntFile's sentinel for
            # "no attachment on this announcement" (observed on BEL/HAL) —
            # normalized to None here so every consumer's existing
            # `if not document_url` guard treats it as absent, rather than
            # attempting an HTTP GET on the string "-".
            attachment_url = row.get("attchmntFile")
            if attachment_url == "-":
                attachment_url = None
            out.append(
                RawAnnouncement(
                    provider="NSE",
                    symbol=symbol,
                    filing_type=_classify_announcement(desc),
                    title=desc or "(untitled)",
                    filing_date=filing_date,
                    published_at=_parse_nse_announcement_datetime(filing_date),
                    document_url=attachment_url,
                    source_url=url,
                    raw=row,
                )
            )
        return out

    async def get_filings(self, symbol: str) -> list[RawAnnouncement]:
        return await self.get_announcements(symbol)

    async def get_annual_reports(self, symbol: str) -> list[RawAnnouncement]:
        """No working NSE annual-report endpoint exists: `api/corp-info?
        corpType=annualreport` returned "Resource not found" for BEL in the
        2026-08-18 bake-off (see REPORT.md §11). Returns [] without making a
        network call — an explicit, documented unresolved gap, not a guess.
        Annual reports must still be archived via the `archive-document` CLI
        command until a working discovery path is found."""
        log.info("nse_source.annual_reports_unresolved", symbol=symbol)
        return []

    async def get_investor_presentations(self, symbol: str) -> list[RawAnnouncement]:
        return [
            a for a in await self.get_announcements(symbol)
            if a.filing_type == "investor_presentation"
        ]

    async def download_attachment(self, document_url: str) -> DownloadedAttachment | None:
        status, body, headers = await self._get_resilient(document_url)
        if status != 200 or not body:
            return None
        is_pdf = body[:4] == b"%PDF"
        is_zip = body[:4] == b"PK\x03\x04"
        return DownloadedAttachment(
            content=body,
            content_type=headers.get("content-type"),
            is_pdf=is_pdf,
            is_zip=is_zip,
        )


def _classify_announcement(text: str | None) -> str:
    """Best-effort categorization from the announcement description/subject
    text — deterministic keyword rules only, never an LLM/guess. Order
    matters: more specific categories are checked before the generic
    "announcement" fallback."""
    t = (text or "").lower()
    if "investor presentation" in t or "investor  presentation" in t:
        return "investor_presentation"
    if "annual report" in t:
        return "annual_report"
    if "transcript" in t or "con call" in t or "concall" in t or "earnings call" in t:
        return "concall_transcript"
    if any(
        phrase in t
        for phrase in (
            "financial result",
            "quarterly result",
            "outcome of board meeting",
            "board meeting outcome",
            "regulation 33",
            "un-audited",
            "unaudited",
            "audited financial",
            "audited result",
        )
    ):
        return "quarterly_result"
    if "order" in t and ("contract" in t or "bagging" in t or "receiving" in t):
        return "order_contract"
    return "announcement"


def _parse_nse_announcement_datetime(value: str | None) -> datetime | None:
    """NSE's announcement feed mixes two date formats across an_dt/sort_date
    ("13-Aug-2026 17:48:45" and "2026-08-13 17:48:45"). Returns None (never
    guesses) if neither format parses."""
    if not value:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None
