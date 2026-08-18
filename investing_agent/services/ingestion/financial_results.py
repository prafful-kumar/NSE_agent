from __future__ import annotations

"""FinancialResultIngestionService: discover -> archive -> parse ->
normalize -> verify -> persist, for quarterly/annual results.

A row that can't be assigned a fiscal period (unparseable period_end date)
is skipped and counted, never guessed into an arbitrary period.
"""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import FinancialResult
from investing_agent.db.repositories.financial import (
    FinancialPeriodRepository,
    FinancialResultRepository,
)
from investing_agent.services.ingestion.common import archive_document, ensure_company
from investing_agent.services.normalization import (
    FinancialResultNormalizer,
    parse_nse_date,
    resolve_fiscal_period,
)
from investing_agent.services.sources.nse_source import NSEDataSource


@dataclass
class FinancialResultSyncResult:
    symbol: str
    rows: list[FinancialResult] = field(default_factory=list)
    new_versions: int = 0
    unchanged: int = 0
    skipped_unparseable: int = 0
    documents_archived: int = 0


class FinancialResultIngestionService:
    def __init__(self, session: AsyncSession, nse: NSEDataSource | None = None) -> None:
        self._session = session
        self._nse = nse or NSEDataSource()
        self._period_repo = FinancialPeriodRepository(session)
        self._result_repo = FinancialResultRepository(session)
        self._normalizer = FinancialResultNormalizer()

    async def sync(self, symbol: str) -> FinancialResultSyncResult:
        symbol = symbol.upper()
        result = FinancialResultSyncResult(symbol=symbol)
        company = await ensure_company(self._session, symbol)

        raw_results, doc_dto = await self._nse.get_quarterly_results(symbol)
        doc, doc_created = await archive_document(self._session, company, doc_dto)
        if doc_created:
            result.documents_archived += 1

        source_type = doc_dto.source_type if doc_dto else "nse_json_hint"
        source_url = doc_dto.source_url if doc_dto else None
        published_at = doc_dto.published_at if doc_dto else None
        source_document_id = doc.id if doc else None

        for raw in raw_results:
            period_end = parse_nse_date(raw.period_end)
            if period_end is None:
                result.skipped_unparseable += 1
                continue

            period_start = parse_nse_date(raw.period_start)
            fiscal_year, quarter, label = resolve_fiscal_period(period_end)
            period = await self._period_repo.get_or_create(
                company_id=company.id,
                period_type="quarter",
                fiscal_year=fiscal_year,
                quarter=quarter,
                period_end=period_end,
                period_start=period_start,
                label=label,
            )

            candidate = self._normalizer.normalize(
                raw,
                company_id=company.id,
                period_id=period.id,
                symbol=symbol,
                source_type=source_type,
                source_url=source_url,
                published_at=published_at,
                source_document_id=source_document_id,
            )
            row, was_new = await self._result_repo.upsert_versioned(candidate)
            result.rows.append(row)
            if was_new:
                result.new_versions += 1
            else:
                result.unchanged += 1

        return result

    async def aclose(self) -> None:
        await self._nse.aclose()
