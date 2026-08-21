from __future__ import annotations

"""Shared ingestion helpers: company resolution and document archival.

Pipeline order enforced here: discover -> archive -> (caller) parse ->
normalize -> verify -> persist. archive_document always runs BEFORE any
normalized row is derived from its content — nothing in this package
normalizes data that hasn't been archived first.
"""

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import Company, SourceDocument
from investing_agent.db.repositories.company import CompanyRepository
from investing_agent.db.repositories.source_document import SourceDocumentRepository
from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.source_documents import SourceDocumentCreate
from investing_agent.services.sources.interfaces import DiscoveredDocument
from investing_agent.services.storage import write_archive


async def ensure_company(session: AsyncSession, symbol: str) -> Company:
    """Get or create a minimal company stub. Company enrichment (sector,
    ISIN, etc.) is handled elsewhere (Phase 3 company-master enrichment) —
    ingestion only needs a stable company_id to hang facts off of."""
    repo = CompanyRepository(session)
    existing = await repo.get_by_symbol(symbol)
    if existing:
        return existing
    return await repo.upsert(
        CompanyCreate(symbol=symbol.upper(), name=symbol.upper(), exchange="NSE")
    )


async def archive_document(
    session: AsyncSession, company: Company, doc: DiscoveredDocument | None
) -> tuple[SourceDocument | None, bool]:
    """Persists a DiscoveredDocument to source_documents, idempotently.
    Returns (row, was_created). Returns (None, False) if there was nothing
    to archive (adapter found no data)."""
    if doc is None:
        return None, False

    content_hash = hashlib.sha256(doc.content).hexdigest()
    storage_path = write_archive(doc.content, doc.company_symbol, content_hash, doc.document_type)
    repo = SourceDocumentRepository(session)
    create = SourceDocumentCreate(
        company_id=company.id,
        symbol=doc.company_symbol,
        exchange=doc.exchange,
        filing_type=doc.filing_type,
        document_type=doc.document_type,
        title=doc.title,
        content_hash=content_hash,
        storage_path=storage_path,
        mime_type=doc.mime_type,
        size_bytes=len(doc.content),
        source_type=doc.source_type,
        source_url=doc.source_url,
        published_at=doc.published_at,
        available_at=doc.published_at,
        data_category="fact",
        parent_document_id=doc.parent_document_id,
    )
    return await repo.get_or_create(create)
