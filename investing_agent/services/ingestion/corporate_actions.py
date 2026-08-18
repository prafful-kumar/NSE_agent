from __future__ import annotations

"""CorporateActionIngestionService: discover -> archive -> parse ->
normalize -> verify -> persist, for dividends/bonus/split/buyback/board
meetings.

Cross-checking: when both NSE and BSE return data for the same symbol, NSE's
candidates are checked against BSE's for corroboration (verify_corporate_
action_cross_source). Today BSE returns nothing (see bse_source.py), so
results stay verification_status="unverified" until BSE access is restored
or a licensed vendor is added — that is the correct, honest outcome per the
accepted bake-off recommendation, not a bug.
"""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import CorporateAction
from investing_agent.db.repositories.corporate_action import CorporateActionRepository
from investing_agent.schemas.corporate_actions import CorporateActionCreate
from investing_agent.services.ingestion.common import archive_document, ensure_company
from investing_agent.services.normalization import CorporateActionNormalizer
from investing_agent.services.sources.bse_source import BSEDataSource
from investing_agent.services.sources.nse_source import NSEDataSource
from investing_agent.services.verification import verify_corporate_action_cross_source


@dataclass
class CorporateActionSyncResult:
    symbol: str
    rows: list[CorporateAction] = field(default_factory=list)
    new_versions: int = 0
    unchanged: int = 0
    skipped_unparseable: int = 0
    documents_archived: int = 0
    verified_count: int = 0


class CorporateActionIngestionService:
    def __init__(
        self,
        session: AsyncSession,
        nse: NSEDataSource | None = None,
        bse: BSEDataSource | None = None,
    ) -> None:
        self._session = session
        self._nse = nse or NSEDataSource()
        self._bse = bse or BSEDataSource()
        self._action_repo = CorporateActionRepository(session)
        self._normalizer = CorporateActionNormalizer()

    async def sync(self, symbol: str) -> CorporateActionSyncResult:
        symbol = symbol.upper()
        result = CorporateActionSyncResult(symbol=symbol)
        company = await ensure_company(self._session, symbol)

        nse_raw, nse_doc_dto = await self._nse.get_corporate_actions(symbol)
        nse_doc, nse_doc_created = await archive_document(self._session, company, nse_doc_dto)
        if nse_doc_created:
            result.documents_archived += 1

        bse_raw, bse_doc_dto = await self._bse.get_corporate_actions(symbol)
        bse_doc, bse_doc_created = await archive_document(self._session, company, bse_doc_dto)
        if bse_doc_created:
            result.documents_archived += 1

        nse_candidates = self._normalize_all(
            nse_raw, company_id=company.id, symbol=symbol,
            source_type=nse_doc_dto.source_type if nse_doc_dto else "nse_json_hint",
            source_url=nse_doc_dto.source_url if nse_doc_dto else None,
            published_at=nse_doc_dto.published_at if nse_doc_dto else None,
            source_document_id=nse_doc.id if nse_doc else None,
            skipped_counter=result,
        )
        bse_candidates = self._normalize_all(
            bse_raw, company_id=company.id, symbol=symbol,
            source_type=bse_doc_dto.source_type if bse_doc_dto else "bse_json_hint",
            source_url=bse_doc_dto.source_url if bse_doc_dto else None,
            published_at=bse_doc_dto.published_at if bse_doc_dto else None,
            source_document_id=bse_doc.id if bse_doc else None,
            skipped_counter=result,
        )

        for candidate in nse_candidates:
            verified = verify_corporate_action_cross_source(candidate, bse_candidates)
            if verified.verification_status == "verified":
                result.verified_count += 1
            row, was_new = await self._action_repo.upsert_versioned(verified)
            result.rows.append(row)
            if was_new:
                result.new_versions += 1
            else:
                result.unchanged += 1

        return result

    def _normalize_all(
        self, raws, *, company_id, symbol, source_type, source_url, published_at,
        source_document_id, skipped_counter: CorporateActionSyncResult,
    ) -> list[CorporateActionCreate]:
        out = []
        for raw in raws:
            candidate = self._normalizer.normalize(
                raw, company_id=company_id, symbol=symbol, source_type=source_type,
                source_url=source_url, published_at=published_at,
                source_document_id=source_document_id,
            )
            if candidate is None:
                skipped_counter.skipped_unparseable += 1
                continue
            out.append(candidate)
        return out

    async def aclose(self) -> None:
        await self._nse.aclose()
        await self._bse.aclose()
