from __future__ import annotations

"""FilingIngestionService: discover -> classify -> selectively auto-archive.

Discovery (get_announcements) is cheap and covers every row NSE returns for
a symbol (frequently 1000+, per the Phase 3B Part B bake-off evidence) —
this service does NOT archive every row. Only categories in
_AUTO_ARCHIVE_TYPES are downloaded and archived automatically; everything
else stays a discovered-but-not-archived RawAnnouncement, with the existing
archive-document CLI command remaining the fallback path for annual reports
(no working NSE endpoint — see nse_source.get_annual_reports) and anything
else that isn't auto-downloaded.

This service explicitly does NOT create any structured FACT rows (no
OrderBookSnapshot, no ManagementGuidance, etc.) — it only gets bytes into
source_documents and, for PDFs, a deterministic text cache. A human still
transcribes structured facts via the record-* CLI commands, citing the
source_document_id this service produces.

Failure handling: SourceAccessError (403/anti-bot block) and
SourceTransientError (retries exhausted) are both caught here and degrade
the sync gracefully (partial results, result.blocked/download_failures
flags) rather than raising out of `sync` — a single bad attachment must
never abort discovery of the rest of a symbol's announcements.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import Company, SourceDocument
from investing_agent.services.extraction.pdf_text import extract_pdf_text, serialize_pages
from investing_agent.services.extraction.zip_archive import UnsafeZipError, extract_zip_members
from investing_agent.services.ingestion.common import archive_document, ensure_company
from investing_agent.services.sources.interfaces import (
    DiscoveredDocument,
    FilingSource,
    RawAnnouncement,
    SourceAccessError,
    SourceTransientError,
)
from investing_agent.services.sources.nse_source import NSEDataSource
from investing_agent.services.storage import write_text_cache

log = structlog.get_logger(__name__)

# Categories worth auto-downloading. Everything outside this allowlist
# (general announcements, order/contract wins, etc.) is still returned in
# RawAnnouncement form by the source for discovery/visibility, just never
# auto-archived — see module docstring.
#
# quarterly_result is included so the manual verification workflow
# (services/verification.py::verify_financial_result_manual, cli.py's
# verify-financial-result) has a Tier-1 primary document — the archived
# filing PDF, with a real announcement timestamp — to check the NSE
# structured-API ("nse_json_hint") FinancialResult rows against, rather
# than relying on secondary financial websites.
_AUTO_ARCHIVE_TYPES = frozenset(
    {"investor_presentation", "concall_transcript", "quarterly_result"}
)


@dataclass
class FilingSyncResult:
    symbol: str
    announcements_discovered: int = 0
    documents_archived: int = 0
    documents_already_archived: int = 0
    zip_children_archived: int = 0
    download_failures: int = 0
    blocked: bool = False  # source signaled an access block (e.g. HTTP 403)
    archived_documents: list[SourceDocument] = field(default_factory=list)


class FilingIngestionService:
    def __init__(self, session: AsyncSession, source: FilingSource | None = None) -> None:
        self._session = session
        self._source = source or NSEDataSource()

    async def sync(
        self,
        symbol: str,
        *,
        since: datetime | None = None,
        filing_types: frozenset[str] | None = None,
    ) -> FilingSyncResult:
        """Discover and archive selected announcement attachments.

        ``since`` is inclusive and is applied to the issuer-provided
        announcement timestamp.  A scoped run skips records with no
        parseable issuer timestamp rather than treating their retrieval time
        as historical availability.  ``filing_types`` narrows (never expands)
        the normal archive allowlist.
        """
        symbol = symbol.upper()
        result = FilingSyncResult(symbol=symbol)
        company = await ensure_company(self._session, symbol)
        allowed_types = filing_types or _AUTO_ARCHIVE_TYPES
        if not allowed_types <= _AUTO_ARCHIVE_TYPES:
            raise ValueError("filing_types must be a subset of the archive allowlist")

        try:
            announcements = await self._source.get_announcements(symbol)
        except SourceAccessError:
            log.warning("filing_sync.blocked", symbol=symbol)
            result.blocked = True
            return result
        except SourceTransientError:
            log.warning("filing_sync.transient_failure", symbol=symbol)
            return result

        result.announcements_discovered = len(announcements)

        for announcement in announcements:
            if announcement.filing_type not in allowed_types:
                continue
            if since is not None and (
                announcement.published_at is None or announcement.published_at < since
            ):
                continue
            if not announcement.document_url:
                continue
            await self._archive_announcement(company, announcement, result)
            if result.blocked:
                break  # a 403 mid-sync means stop, not keep hammering NSE

        return result

    async def _archive_announcement(
        self, company: Company, announcement: RawAnnouncement, result: FilingSyncResult
    ) -> None:
        try:
            attachment = await self._source.download_attachment(announcement.document_url)
        except SourceAccessError:
            log.warning("filing_sync.download_blocked", url=announcement.document_url)
            result.blocked = True
            return
        except SourceTransientError:
            log.warning("filing_sync.download_failed", url=announcement.document_url)
            result.download_failures += 1
            return

        if attachment is None:
            result.download_failures += 1
            return

        if attachment.is_pdf:
            document_type = "pdf"
        elif attachment.is_zip:
            document_type = "zip"
        else:
            log.info(
                "filing_sync.unsupported_attachment",
                url=announcement.document_url,
                content_type=attachment.content_type,
            )
            return

        fetched_at = datetime.now(UTC)
        doc = DiscoveredDocument(
            company_symbol=company.symbol,
            exchange="NSE",
            source="NSE",
            source_type="nse_filing_zip" if document_type == "zip" else "nse_filing_pdf",
            filing_type=announcement.filing_type,
            document_type=document_type,
            title=announcement.title,
            content=attachment.content,
            source_url=announcement.document_url,
            fetched_at=fetched_at,
            published_at=announcement.published_at,
            mime_type=attachment.content_type,
        )
        row, was_created = await archive_document(self._session, company, doc)
        if row is None:
            return
        if was_created:
            result.documents_archived += 1
            result.archived_documents.append(row)
            if document_type == "pdf":
                self._cache_pdf_text(row, attachment.content)
        else:
            result.documents_already_archived += 1

        if document_type == "zip":
            await self._archive_zip_children(company, announcement, row, attachment.content, result)

    def _cache_pdf_text(self, row: SourceDocument, content: bytes) -> None:
        if not row.storage_path:
            return
        try:
            pages = extract_pdf_text(content)
        except Exception:
            log.warning("filing_sync.pdf_text_extraction_failed", storage_path=row.storage_path)
            return
        write_text_cache(row.storage_path, serialize_pages(pages))

    async def _archive_zip_children(
        self,
        company: Company,
        announcement: RawAnnouncement,
        parent_row: SourceDocument,
        zip_content: bytes,
        result: FilingSyncResult,
    ) -> None:
        try:
            members = extract_zip_members(zip_content)
        except UnsafeZipError as exc:
            log.warning("filing_sync.unsafe_zip", url=announcement.document_url, error=str(exc))
            return

        fetched_at = datetime.now(UTC)
        for member in members:
            child_doc = DiscoveredDocument(
                company_symbol=company.symbol,
                exchange="NSE",
                source="NSE",
                source_type="nse_filing_zip_member",
                filing_type=announcement.filing_type,
                document_type=member.document_type,
                title=f"{announcement.title} — {member.filename}",
                content=member.content,
                source_url=announcement.document_url,
                fetched_at=fetched_at,
                published_at=announcement.published_at,
                mime_type=None,
                parent_document_id=parent_row.id,
            )
            child_row, was_created = await archive_document(self._session, company, child_doc)
            if child_row is None:
                continue
            if was_created:
                result.zip_children_archived += 1
                result.archived_documents.append(child_row)
                if member.document_type == "pdf":
                    self._cache_pdf_text(child_row, member.content)

    async def aclose(self) -> None:
        aclose = getattr(self._source, "aclose", None)
        if aclose is not None:
            await aclose()
