from __future__ import annotations

"""Repository for source_documents / document_versions.

Idempotency lives here: get_or_create looks up by the same unique key the
DB enforces (company_id, source_url, content_hash) before inserting, so
re-running ingestion against an unchanged source is a no-op rather than a
duplicate row or an IntegrityError.
"""

import uuid

from sqlalchemy import select

from investing_agent.db.models import DocumentVersion, SourceDocument
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.source_documents import SourceDocumentCreate


class SourceDocumentRepository(BaseRepository[SourceDocument]):
    model = SourceDocument

    async def list_by_company(self, company_id: uuid.UUID) -> list[SourceDocument]:
        result = await self.session.execute(
            select(SourceDocument)
            .where(SourceDocument.company_id == company_id)
            .order_by(SourceDocument.available_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_hash(
        self, company_id: uuid.UUID, source_url: str, content_hash: str
    ) -> SourceDocument | None:
        result = await self.session.execute(
            select(SourceDocument).where(
                SourceDocument.company_id == company_id,
                SourceDocument.source_url == source_url,
                SourceDocument.content_hash == content_hash,
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self, data: SourceDocumentCreate
    ) -> tuple[SourceDocument, bool]:
        """Returns (document, was_created). Idempotent on
        (company_id, source_url, content_hash)."""
        existing = await self.get_by_hash(data.company_id, data.source_url, data.content_hash)
        if existing:
            return existing, False

        doc = SourceDocument(
            company_id=data.company_id,
            symbol=data.symbol,
            exchange=data.exchange,
            filing_type=data.filing_type,
            document_type=data.document_type,
            title=data.title,
            period_id=data.period_id,
            content_hash=data.content_hash,
            storage_path=data.storage_path,
            mime_type=data.mime_type,
            size_bytes=data.size_bytes,
            source_type=data.source_type,
            source_url=data.source_url,
            published_at=data.published_at,
            data_category=data.data_category,
            confidence=data.confidence,
            parent_document_id=data.parent_document_id,
        )
        await self.add(doc)

        # Every new document starts its own version-1 chain.
        version = DocumentVersion(
            source_document_id=doc.id,
            version_number=1,
            status="original",
        )
        self.session.add(version)
        await self.session.flush()

        return doc, True

    async def record_amendment(
        self, prior: SourceDocument, new: SourceDocument, notes: str | None = None
    ) -> DocumentVersion:
        """Link a newly discovered document as an amendment of a prior one:
        creates version N+1 for `new` and points prior's latest version
        forward via superseded_by_id. Neither row is ever deleted/overwritten.
        """
        result = await self.session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.source_document_id == prior.id)
            .order_by(DocumentVersion.version_number.desc())
            .limit(1)
        )
        prior_version = result.scalar_one_or_none()
        prior_version_number = prior_version.version_number if prior_version else 1

        new_version = DocumentVersion(
            source_document_id=new.id,
            version_number=prior_version_number + 1,
            status="amended",
            notes=notes,
        )
        self.session.add(new_version)
        await self.session.flush()

        if prior_version:
            prior_version.superseded_by_id = new_version.id
            await self.session.flush()

        return new_version
