from __future__ import annotations

"""Unit tests for the Phase 3B CLI commands (archive-document, show-filings,
extract-text, record-*) via Click's CliRunner.

The DB/session layer is mocked throughout — these tests exercise CLI
argument parsing, default extraction_method/verification_status, and the
--verify/--verified-by contract, not real persistence (that's covered by the
Postgres-gated integration tests).
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from investing_agent.cli import cli


class _FakeSessionCM:
    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc) -> bool:
        return False


def _fake_session_factory(session):
    return lambda: _FakeSessionCM(session)


class TestArchiveDocumentCommand:
    def test_archives_pdf_and_reports_storage_path(self, tmp_path) -> None:
        pdf_path = tmp_path / "presentation.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake content")

        session = AsyncMock()
        company = MagicMock(id=uuid.uuid4(), symbol="BEL")
        archived_row = MagicMock(
            id=uuid.uuid4(), title="Q1 Investor Presentation",
            storage_path="BEL/abc123.pdf", content_hash="abc123",
        )

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.ingestion.common.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.services.ingestion.common.archive_document",
                AsyncMock(return_value=(archived_row, True)),
            ) as archive_mock,
        ):
            result = CliRunner().invoke(
                cli,
                [
                    "archive-document", "BEL", "--file", str(pdf_path),
                    "--filing-type", "investor_presentation",
                    "--title", "Q1 Investor Presentation",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "Archived: Q1 Investor Presentation" in result.output
        assert "storage_path      : BEL/abc123.pdf" in result.output

        doc_arg = archive_mock.call_args.args[2]
        assert doc_arg.document_type == "pdf"
        assert doc_arg.filing_type == "investor_presentation"
        assert doc_arg.content == b"%PDF-1.4 fake content"

    def test_idempotent_reingest_reports_already_archived(self, tmp_path) -> None:
        pdf_path = tmp_path / "doc.pdf"
        pdf_path.write_bytes(b"%PDF fake")

        session = AsyncMock()
        company = MagicMock(id=uuid.uuid4(), symbol="BEL")
        existing_row = MagicMock(
            id=uuid.uuid4(), title="Existing", storage_path="BEL/x.pdf", content_hash="x",
        )

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.ingestion.common.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.services.ingestion.common.archive_document",
                AsyncMock(return_value=(existing_row, False)),
            ),
        ):
            result = CliRunner().invoke(
                cli,
                [
                    "archive-document", "BEL", "--file", str(pdf_path),
                    "--filing-type", "annual_report", "--title", "Existing",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "Already archived (idempotent)" in result.output


class TestRecordOrderBookCommand:
    def _invoke(
        self, extra_args: list[str], row: MagicMock, session: AsyncMock, company: MagicMock
    ):
        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.ingestion.common.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.db.repositories.company_research.OrderBookSnapshotRepository"
            ) as repo_cls,
        ):
            repo_cls.return_value.create = AsyncMock(return_value=row)
            result = CliRunner().invoke(
                cli,
                [
                    "record-order-book", "BEL",
                    "--source-document-id", str(uuid.uuid4()),
                    "--as-of-date", "2026-06-30",
                    "--value", "75000",
                    *extra_args,
                ],
            )
            create_call = repo_cls.return_value.create.call_args
        return result, create_call

    def test_defaults_to_manual_unverified(self) -> None:
        session = AsyncMock()
        company = MagicMock(id=uuid.uuid4(), symbol="BEL")
        row = MagicMock(id=uuid.uuid4(), verification_status="UNVERIFIED")

        result, create_call = self._invoke([], row, session, company)

        assert result.exit_code == 0, result.output
        data = create_call.args[0]
        assert data.extraction_method == "MANUAL"
        assert data.verification_status == "UNVERIFIED"
        assert data.verified_by is None

    def test_verify_flag_requires_verified_by(self) -> None:
        session = AsyncMock()
        company = MagicMock(id=uuid.uuid4(), symbol="BEL")
        row = MagicMock(id=uuid.uuid4(), verification_status="UNVERIFIED")

        result, _ = self._invoke(["--verify"], row, session, company)

        assert result.exit_code != 0
        assert "--verify requires --verified-by" in result.output

    def test_verify_with_verified_by_sets_human_verified(self) -> None:
        session = AsyncMock()
        company = MagicMock(id=uuid.uuid4(), symbol="BEL")
        row = MagicMock(id=uuid.uuid4(), verification_status="HUMAN_VERIFIED")

        result, create_call = self._invoke(
            ["--verify", "--verified-by", "analyst@example.com"], row, session, company
        )

        assert result.exit_code == 0, result.output
        data = create_call.args[0]
        assert data.verification_status == "HUMAN_VERIFIED"
        assert data.verified_by == "analyst@example.com"
        assert data.verified_at is not None


class TestRecordCommentaryCommand:
    def test_quote_used_as_own_source_quote(self) -> None:
        session = AsyncMock()
        company = MagicMock(id=uuid.uuid4(), symbol="BEL")
        row = MagicMock(id=uuid.uuid4(), verification_status="UNVERIFIED")

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.ingestion.common.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.db.repositories.company_research.ManagementCommentaryRepository"
            ) as repo_cls,
        ):
            repo_cls.return_value.create = AsyncMock(return_value=row)
            result = CliRunner().invoke(
                cli,
                [
                    "record-commentary", "BEL",
                    "--source-document-id", str(uuid.uuid4()),
                    "--quote", "We expect double-digit growth in FY27.",
                    "--speaker", "CMD",
                ],
            )
            data = repo_cls.return_value.create.call_args.args[0]

        assert result.exit_code == 0, result.output
        assert data.quote == "We expect double-digit growth in FY27."
        assert data.source_quote == "We expect double-digit growth in FY27."
        assert data.speaker == "CMD"
        assert data.extraction_method == "MANUAL"
        assert data.verification_status == "UNVERIFIED"


class TestShowFilingsCommand:
    def test_lists_archived_documents(self) -> None:
        session = AsyncMock()
        company = MagicMock(id=uuid.uuid4(), symbol="BEL")
        docs = [
            MagicMock(
                id=uuid.uuid4(), filing_type="investor_presentation",
                title="Q1 IP", published_at=datetime(2026, 6, 1, tzinfo=UTC),
            ),
        ]

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.company.CompanyRepository"
            ) as company_repo_cls,
            patch(
                "investing_agent.db.repositories.source_document.SourceDocumentRepository"
            ) as doc_repo_cls,
        ):
            company_repo_cls.return_value.get_by_symbol = AsyncMock(return_value=company)
            doc_repo_cls.return_value.list_by_company = AsyncMock(return_value=docs)
            result = CliRunner().invoke(cli, ["show-filings", "BEL"])

        assert result.exit_code == 0, result.output
        assert "Q1 IP" in result.output
        assert "investor_presentation" in result.output

    def test_unknown_symbol_exits_nonzero(self) -> None:
        session = AsyncMock()

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.db.repositories.company.CompanyRepository"
            ) as company_repo_cls,
        ):
            company_repo_cls.return_value.get_by_symbol = AsyncMock(return_value=None)
            result = CliRunner().invoke(cli, ["show-filings", "NOPE"])

        assert result.exit_code != 0


class TestExtractTextCommand:
    def test_prints_cached_text_without_re_extracting(self) -> None:
        doc_id = uuid.uuid4()
        doc = MagicMock(id=doc_id, storage_path="BEL/abc.pdf", document_type="pdf")
        session = AsyncMock()
        session.get = AsyncMock(return_value=doc)

        with (
            patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)),
            patch(
                "investing_agent.services.storage.read_text_cache",
                return_value="Hello World",
            ),
            patch("investing_agent.services.extraction.pdf_text.extract_pdf_text") as extract_mock,
        ):
            result = CliRunner().invoke(cli, ["extract-text", str(doc_id)])

        assert result.exit_code == 0, result.output
        assert "Hello World" in result.output
        extract_mock.assert_not_called()

    def test_missing_document_exits_nonzero(self) -> None:
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)

        with patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)):
            result = CliRunner().invoke(cli, ["extract-text", str(uuid.uuid4())])

        assert result.exit_code != 0

    def test_non_pdf_document_rejected(self) -> None:
        doc = MagicMock(storage_path="BEL/abc.html", document_type="html")
        session = AsyncMock()
        session.get = AsyncMock(return_value=doc)

        with patch("investing_agent.db.session.AsyncSessionLocal", _fake_session_factory(session)):
            result = CliRunner().invoke(cli, ["extract-text", str(uuid.uuid4())])

        assert result.exit_code != 0
        assert "only supports pdf" in result.output
