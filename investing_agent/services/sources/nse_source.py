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
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from investing_agent.services.sources.interfaces import (
    CorporateActionSource,
    DiscoveredDocument,
    FinancialResultSource,
    RawCorporateAction,
    RawFinancialResult,
)

log = structlog.get_logger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; investing-agent/1.0; +research use)"}
_MIN_REQUEST_GAP_SECONDS = 1.5
# "A" = audited, "U" = unaudited/limited-review; anything else is left
# unresolved rather than guessed.
_RES_TYPE_AUDITED = {"A": True, "U": False}


class NSEDataSource(CorporateActionSource, FinancialResultSource):
    """FilingSource is deliberately NOT implemented: no NSE document-listing
    endpoint was verified working in the bake-off (the corporate-filings
    page is a JS-rendered SPA). Implementing it would mean fabricating an
    untested scraper — out of scope per "report unresolved issues instead
    of guessing." Revisit once a real filing-URL discovery path is verified.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(headers=_HEADERS, timeout=15.0)
        self._last_request_at: float = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str) -> tuple[int, bytes]:
        elapsed = asyncio.get_event_loop().time() - self._last_request_at
        if elapsed < _MIN_REQUEST_GAP_SECONDS:
            await asyncio.sleep(_MIN_REQUEST_GAP_SECONDS - elapsed)
        resp = await self._client.get(url)
        self._last_request_at = asyncio.get_event_loop().time()
        log.info("nse_source.request", url=url, status=resp.status_code)
        return resp.status_code, resp.content

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
