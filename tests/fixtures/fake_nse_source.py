from __future__ import annotations

"""FakeNSEDataSource — reads the BEL/HAL fixture JSON captured live during
the Phase 3 bake-off (tests/fixtures/nse/*.json) instead of hitting the
network. Same parsing logic as NSEDataSource, so ingestion-service tests
exercise the real normalization path end-to-end without network access.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from investing_agent.services.sources.interfaces import (
    CorporateActionSource,
    DiscoveredDocument,
    FinancialResultSource,
    RawCorporateAction,
    RawFinancialResult,
)

_FIXTURES_DIR = Path(__file__).parent / "nse"
_RES_TYPE_AUDITED = {"A": True, "U": False}


class FakeNSEDataSource(CorporateActionSource, FinancialResultSource):
    def __init__(self, mutate_content: bool = False) -> None:
        """mutate_content=True appends a byte to every fixture body before
        returning it, so tests can simulate "the source changed" without a
        second fixture file."""
        self._mutate_content = mutate_content

    async def aclose(self) -> None:
        pass

    def _load(self, filename: str) -> bytes:
        body = (_FIXTURES_DIR / filename).read_bytes()
        if self._mutate_content:
            body = body[:-1] + b" "  # trailing whitespace inside valid JSON is harmless
        return body

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
        filename = f"{symbol.lower()}_corporate_actions.json"
        if not (_FIXTURES_DIR / filename).exists():
            return [], None
        body = self._load(filename)
        now = datetime.now(UTC)

        doc = DiscoveredDocument(
            company_symbol=symbol,
            exchange="NSE",
            source="NSE",
            source_type="nse_json_hint",
            filing_type="announcement",
            document_type="json",
            title=f"NSE corporate actions hint — {symbol}",
            content=body,
            source_url=f"https://www.nseindia.com/api/corporates-corporateActions?symbol={symbol}",
            fetched_at=now,
        )

        rows = json.loads(body)
        out: list[RawCorporateAction] = []
        for row in rows:
            subject = (row.get("subject") or "").lower()
            action_type = (
                "dividend" if "dividend" in subject
                else "bonus" if "bonus" in subject
                else "split" if "split" in subject
                else "other"
            )
            dividend_type = (
                "interim" if "interim" in subject
                else "special" if "special" in subject
                else None
            )
            out.append(
                RawCorporateAction(
                    action_type=action_type,
                    announced_date=row.get("caBroadcastDate"),
                    event_date=row.get("exDate"),
                    ex_date=row.get("exDate"),
                    record_date=row.get("recDate"),
                    payment_date=None,
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
        filename = f"{symbol.lower()}_quarterly_results.json"
        if not (_FIXTURES_DIR / filename).exists():
            return [], None
        body = self._load(filename)
        now = datetime.now(UTC)

        doc = DiscoveredDocument(
            company_symbol=symbol,
            exchange="NSE",
            source="NSE",
            source_type="nse_json_hint",
            filing_type="quarterly_result",
            document_type="json",
            title=f"NSE quarterly results hint — {symbol}",
            content=body,
            source_url=f"https://www.nseindia.com/api/results-comparision?symbol={symbol}",
            fetched_at=now,
        )

        payload = json.loads(body)
        rows = payload.get("resCmpData", [])
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
        return [], None
