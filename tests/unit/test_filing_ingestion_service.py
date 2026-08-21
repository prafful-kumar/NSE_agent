from __future__ import annotations

"""Unit tests for FilingIngestionService: discover -> classify -> selective
auto-archive (investor_presentation/concall_transcript only) -> ZIP-child
extraction -> idempotent archival.

archive_document is faked (not mocked as a dumb stub) via FakeArchiveStore,
which mimics SourceDocumentRepository.get_or_create's real idempotency key
(company_id, source_url, content_hash) so duplicate-discovery and
content-hash-idempotency tests exercise real dedup logic, not a mock that
always says "created". No network, no filesystem, no DB.
"""

import hashlib
import uuid
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from investing_agent.services.ingestion.filings import FilingIngestionService
from investing_agent.services.sources.interfaces import (
    DownloadedAttachment,
    FilingSource,
    RawAnnouncement,
    SourceAccessError,
    SourceTransientError,
)


def _company(symbol: str = "BEL") -> MagicMock:
    c = MagicMock()
    c.id = uuid.uuid4()
    c.symbol = symbol
    return c


def _announcement(
    filing_type: str,
    title: str,
    document_url: str | None,
    symbol: str = "BEL",
    published_at: datetime | None = datetime(2026, 8, 10, tzinfo=UTC),
) -> RawAnnouncement:
    return RawAnnouncement(
        provider="NSE",
        symbol=symbol,
        filing_type=filing_type,
        title=title,
        filing_date="10-Aug-2026 17:35:59",
        published_at=published_at,
        document_url=document_url,
        source_url=f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}",
        raw={},
    )


class FakeFilingSource(FilingSource):
    def __init__(
        self,
        announcements: list[RawAnnouncement] | None = None,
        downloads: dict[str, DownloadedAttachment] | None = None,
        raise_on_announcements: Exception | None = None,
        raise_on_download: dict[str, Exception] | None = None,
    ) -> None:
        self._announcements = announcements or []
        self._downloads = downloads or {}
        self._raise_on_announcements = raise_on_announcements
        self._raise_on_download = raise_on_download or {}
        self.download_calls: list[str] = []
        self.annual_reports_called = False

    async def get_announcements(self, symbol: str) -> list[RawAnnouncement]:
        if self._raise_on_announcements:
            raise self._raise_on_announcements
        return self._announcements

    async def get_filings(self, symbol: str) -> list[RawAnnouncement]:
        return await self.get_announcements(symbol)

    async def get_annual_reports(self, symbol: str) -> list[RawAnnouncement]:
        self.annual_reports_called = True
        return []

    async def get_investor_presentations(self, symbol: str) -> list[RawAnnouncement]:
        return [a for a in self._announcements if a.filing_type == "investor_presentation"]

    async def download_attachment(self, document_url: str) -> DownloadedAttachment | None:
        self.download_calls.append(document_url)
        if document_url in self._raise_on_download:
            raise self._raise_on_download[document_url]
        return self._downloads.get(document_url)

    async def aclose(self) -> None:
        pass


class FakeArchiveStore:
    """Mimics SourceDocumentRepository.get_or_create's idempotency key
    (company_id, source_url, content_hash) — a dict-backed fake, not a
    dumb "always created" stub, so idempotency tests exercise real dedup."""

    def __init__(self) -> None:
        self._rows: dict[tuple, MagicMock] = {}
        self.create_calls = 0

    async def __call__(self, session, company, doc):
        if doc is None:
            return None, False
        content_hash = hashlib.sha256(doc.content).hexdigest()
        key = (company.id, doc.source_url, content_hash)
        if key in self._rows:
            return self._rows[key], False
        row = MagicMock()
        row.id = uuid.uuid4()
        row.title = doc.title
        row.filing_type = doc.filing_type
        row.document_type = doc.document_type
        row.published_at = doc.published_at
        row.available_at = doc.published_at
        row.source_url = doc.source_url
        row.source_type = doc.source_type
        row.storage_path = f"{doc.company_symbol}/{content_hash}.{doc.document_type}"
        row.content_hash = content_hash
        row.parent_document_id = doc.parent_document_id
        self._rows[key] = row
        self.create_calls += 1
        return row, True


def _zip_with_pdf_and_image() -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("presentation.pdf", b"%PDF-1.4 inner pdf")
        zf.writestr("logo.png", b"\x89PNG not-a-document")
    return buf.getvalue()


def _service(source: FilingSource) -> FilingIngestionService:
    svc = FilingIngestionService.__new__(FilingIngestionService)
    svc._session = MagicMock()
    svc._source = source
    return svc


def _patched(company: MagicMock, store: FakeArchiveStore):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "investing_agent.services.ingestion.filings.ensure_company",
            AsyncMock(return_value=company),
        )
    )
    # store is itself an async callable (FakeArchiveStore.__call__) — patched
    # in directly rather than wrapped in AsyncMock(side_effect=...), since an
    # AsyncMock whose side_effect is itself async double-wraps the coroutine.
    stack.enter_context(
        patch("investing_agent.services.ingestion.filings.archive_document", store)
    )
    stack.enter_context(patch("investing_agent.services.ingestion.filings.write_text_cache"))
    stack.enter_context(
        patch("investing_agent.services.ingestion.filings.extract_pdf_text", return_value=[])
    )
    return stack


class TestAnnouncementDiscoveryAndFiltering:
    @pytest.mark.asyncio
    async def test_bel_discovery_counts_all_announcements(self) -> None:
        company = _company("BEL")
        store = FakeArchiveStore()
        source = FakeFilingSource(
            announcements=[
                _announcement("order_contract", "Bagging of orders", "https://x/order.pdf"),
                _announcement("announcement", "Change in Director(s)", "https://x/dir.pdf"),
                _announcement(
                    "investor_presentation", "Investor Presentation", "https://x/ip.pdf"
                ),
            ]
        )
        svc = _service(source)

        with _patched(company, store):
            result = await svc.sync("bel")

        assert result.symbol == "BEL"
        assert result.announcements_discovered == 3
        # only the investor_presentation row is worth downloading; the other
        # two categories are discovered but left for the manual path. The
        # fake has no bytes registered for it, so the attempted download
        # comes back empty rather than archived.
        assert source.download_calls == ["https://x/ip.pdf"]
        assert result.documents_archived == 0
        assert result.download_failures == 1

    @pytest.mark.asyncio
    async def test_non_allowlisted_categories_never_trigger_download(self) -> None:
        company = _company("BEL")
        store = FakeArchiveStore()
        source = FakeFilingSource(
            announcements=[
                _announcement("order_contract", "Bagging of orders", "https://x/order.pdf"),
                _announcement("announcement", "General update", "https://x/gen.pdf"),
                _announcement("annual_report", "Annual Report FY26", "https://x/ar.pdf"),
            ]
        )
        svc = _service(source)
        with _patched(company, store):
            result = await svc.sync("BEL")

        assert result.documents_archived == 0
        assert source.download_calls == []
        assert source.annual_reports_called is False  # never touched — manual path only


class TestInvestorPresentationArchival:
    @pytest.mark.asyncio
    async def test_pdf_investor_presentation_archived(self) -> None:
        company = _company("BEL")
        store = FakeArchiveStore()
        url = "https://nsearchives.nseindia.com/corporate/BEL_ip.pdf"
        source = FakeFilingSource(
            announcements=[_announcement("investor_presentation", "Investor Presentation", url)],
            downloads={
                url: DownloadedAttachment(
                    content=b"%PDF-1.4 fake ip content",
                    content_type="application/pdf",
                    is_pdf=True,
                    is_zip=False,
                )
            },
        )
        svc = _service(source)
        with _patched(company, store):
            result = await svc.sync("BEL")

        assert result.documents_archived == 1
        assert source.download_calls == [url]
        archived = result.archived_documents[0]
        assert archived.document_type == "pdf"
        assert archived.filing_type == "investor_presentation"

    @pytest.mark.asyncio
    async def test_zip_investor_presentation_extracts_pdf_child_with_parent_link(self) -> None:
        company = _company("BEL")
        store = FakeArchiveStore()
        url = "https://nsearchives.nseindia.com/corporate/NSE_ip.zip"
        zip_bytes = _zip_with_pdf_and_image()
        source = FakeFilingSource(
            announcements=[_announcement("investor_presentation", "Investor Presentation", url)],
            downloads={
                url: DownloadedAttachment(
                    content=zip_bytes, content_type="application/zip", is_pdf=False, is_zip=True
                )
            },
        )
        svc = _service(source)
        with _patched(company, store):
            result = await svc.sync("BEL")

        assert result.documents_archived == 1  # the zip itself
        assert result.zip_children_archived == 1  # only presentation.pdf, logo.png skipped
        parent = next(d for d in result.archived_documents if d.document_type == "zip")
        child = next(d for d in result.archived_documents if d.document_type == "pdf")
        assert child.parent_document_id == parent.id

    @pytest.mark.asyncio
    async def test_non_pdf_non_zip_attachment_skipped_not_archived(self) -> None:
        company = _company("BEL")
        store = FakeArchiveStore()
        url = "https://nsearchives.nseindia.com/corporate/BEL_ip.pdf"
        source = FakeFilingSource(
            announcements=[_announcement("investor_presentation", "Investor Presentation", url)],
            downloads={
                url: DownloadedAttachment(
                    content=b"<html>anti-bot challenge</html>",
                    content_type="application/pdf",
                    is_pdf=False,
                    is_zip=False,
                )
            },
        )
        svc = _service(source)
        with _patched(company, store):
            result = await svc.sync("BEL")

        assert result.documents_archived == 0
        assert result.download_failures == 0  # a successful-but-unsupported download, not a failure


class TestFinancialResultArchival:
    @pytest.mark.asyncio
    async def test_scoped_result_sync_archives_pdf_and_preserves_issuer_timestamp(self) -> None:
        company = _company("BEL")
        store = FakeArchiveStore()
        url = "https://nsearchives.nseindia.com/corporate/BEL_results.pdf"
        source = FakeFilingSource(
            announcements=[
                _announcement("quarterly_result", "Outcome of Board Meeting - Results", url),
                _announcement("announcement", "Board Meeting Intimation", "https://x/ignore.pdf"),
            ],
            downloads={
                url: DownloadedAttachment(
                    content=b"%PDF-1.4 result filing",
                    content_type="application/pdf",
                    is_pdf=True,
                    is_zip=False,
                )
            },
        )
        svc = _service(source)
        with _patched(company, store):
            result = await svc.sync(
                "BEL",
                since=datetime(2026, 8, 1, tzinfo=UTC),
                filing_types=frozenset({"quarterly_result"}),
            )

        assert source.download_calls == [url]
        assert result.documents_archived == 1
        archived = result.archived_documents[0]
        assert archived.filing_type == "quarterly_result"
        assert archived.published_at == datetime(2026, 8, 10, tzinfo=UTC)
        assert archived.available_at == datetime(2026, 8, 10, tzinfo=UTC)
        assert archived.source_type == "nse_filing_pdf"

    @pytest.mark.asyncio
    async def test_scoped_result_sync_excludes_old_and_timestampless_filings(self) -> None:
        company = _company("BEL")
        store = FakeArchiveStore()
        old = _announcement(
            "quarterly_result",
            "FY25 Results",
            "https://x/old.pdf",
            published_at=datetime(2026, 3, 31, tzinfo=UTC),
        )
        unknown_time = _announcement(
            "quarterly_result", "Unknown date", "https://x/unknown.pdf", published_at=None
        )
        source = FakeFilingSource(announcements=[old, unknown_time])
        svc = _service(source)
        with _patched(company, store):
            result = await svc.sync(
                "BEL",
                since=datetime(2026, 4, 1, tzinfo=UTC),
                filing_types=frozenset({"quarterly_result"}),
            )

        assert result.documents_archived == 0
        assert source.download_calls == []


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_duplicate_discovery_within_one_sync_archives_once(self) -> None:
        company = _company("BEL")
        store = FakeArchiveStore()
        url = "https://nsearchives.nseindia.com/corporate/BEL_ip.pdf"
        source = FakeFilingSource(
            announcements=[
                _announcement("investor_presentation", "Investor Presentation", url),
                _announcement("investor_presentation", "Investor Presentation (re-listed)", url),
            ],
            downloads={
                url: DownloadedAttachment(
                    content=b"%PDF-1.4 identical bytes", content_type="application/pdf",
                    is_pdf=True, is_zip=False,
                )
            },
        )
        svc = _service(source)
        with _patched(company, store):
            result = await svc.sync("BEL")

        assert result.documents_archived == 1
        assert result.documents_already_archived == 1
        assert store.create_calls == 1

    @pytest.mark.asyncio
    async def test_content_hash_idempotency_across_two_syncs(self) -> None:
        company = _company("BEL")
        store = FakeArchiveStore()
        url = "https://nsearchives.nseindia.com/corporate/BEL_ip.pdf"
        source = FakeFilingSource(
            announcements=[_announcement("investor_presentation", "Investor Presentation", url)],
            downloads={
                url: DownloadedAttachment(
                    content=b"%PDF-1.4 stable bytes", content_type="application/pdf",
                    is_pdf=True, is_zip=False,
                )
            },
        )
        svc = _service(source)
        with _patched(company, store):
            first = await svc.sync("BEL")
            second = await svc.sync("BEL")

        assert first.documents_archived == 1
        assert second.documents_archived == 0
        assert second.documents_already_archived == 1
        assert store.create_calls == 1  # unchanged bytes never create a second row


class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_blocked_at_discovery_returns_result_without_raising(self) -> None:
        company = _company("BEL")
        store = FakeArchiveStore()
        source = FakeFilingSource(raise_on_announcements=SourceAccessError("403 blocked"))
        svc = _service(source)
        with _patched(company, store):
            result = await svc.sync("BEL")

        assert result.blocked is True
        assert result.announcements_discovered == 0

    @pytest.mark.asyncio
    async def test_transient_error_at_discovery_returns_result_without_raising(self) -> None:
        company = _company("BEL")
        store = FakeArchiveStore()
        source = FakeFilingSource(raise_on_announcements=SourceTransientError("network blip"))
        svc = _service(source)
        with _patched(company, store):
            result = await svc.sync("BEL")

        assert result.blocked is False
        assert result.announcements_discovered == 0

    @pytest.mark.asyncio
    async def test_blocked_during_download_stops_remaining_announcements(self) -> None:
        company = _company("BEL")
        store = FakeArchiveStore()
        blocked_url = "https://x/blocked.pdf"
        later_url = "https://x/later.pdf"
        source = FakeFilingSource(
            announcements=[
                _announcement("investor_presentation", "IP 1", blocked_url),
                _announcement("investor_presentation", "IP 2", later_url),
            ],
            raise_on_download={blocked_url: SourceAccessError("403 blocked")},
        )
        svc = _service(source)
        with _patched(company, store):
            result = await svc.sync("BEL")

        assert result.blocked is True
        assert later_url not in source.download_calls  # stopped, didn't keep hammering

    @pytest.mark.asyncio
    async def test_transient_download_failure_counted_not_fatal(self) -> None:
        company = _company("BEL")
        store = FakeArchiveStore()
        url = "https://x/flaky.pdf"
        source = FakeFilingSource(
            announcements=[_announcement("investor_presentation", "IP", url)],
            raise_on_download={url: SourceTransientError("retries exhausted")},
        )
        svc = _service(source)
        with _patched(company, store):
            result = await svc.sync("BEL")

        assert result.blocked is False
        assert result.download_failures == 1
        assert result.documents_archived == 0
