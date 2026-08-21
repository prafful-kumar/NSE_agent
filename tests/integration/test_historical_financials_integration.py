from __future__ import annotations

"""Integration test for services/ingestion/historical_financials.py against
a real PostgreSQL database.

Core rule under test: a manually-transcribed historical quarter is created
already verification_status="verified" with available_at set to the
archived document's own real published_at — never ingestion time. This is
the same PIT discipline the Phase 5B fix exists to protect, now exercised
for the new manual-transcription path.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.source_documents import SourceDocumentCreate
from investing_agent.services.ingestion.historical_financials import (
    HistoricalFinancialTranscriptionError,
    record_historical_financial_result,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session():
    import os

    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://investing:investing@localhost:5433/investing_agent_test"
    )
    from investing_agent.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def company(db_session):
    from investing_agent.db.repositories.company import CompanyRepository

    repo = CompanyRepository(db_session)
    return await repo.upsert(
        CompanyCreate(
            symbol=f"BEL-{uuid.uuid4().hex[:8]}", name="Bharat Electronics", exchange="NSE"
        )
    )


@pytest.fixture
async def primary_document(db_session, company):
    from investing_agent.db.repositories.source_document import SourceDocumentRepository

    repo = SourceDocumentRepository(db_session)
    doc, _ = await repo.get_or_create(
        SourceDocumentCreate(
            company_id=company.id, symbol=company.symbol, exchange="NSE",
            filing_type="quarterly_result", document_type="pdf",
            title="Financial Result Updates", content_hash="hist" * 16,
            source_type="nse_filing_pdf", source_url="https://nse.example/hist.pdf",
            published_at=datetime(2022, 2, 10, 14, 25, 42, tzinfo=UTC),
            data_category="fact",
        )
    )
    return doc


@pytest.fixture
async def json_hint_document(db_session, company):
    from investing_agent.db.repositories.source_document import SourceDocumentRepository

    repo = SourceDocumentRepository(db_session)
    doc, _ = await repo.get_or_create(
        SourceDocumentCreate(
            company_id=company.id, symbol=company.symbol, exchange="NSE",
            filing_type="quarterly_result", document_type="json",
            title="NSE quarterly results hint", content_hash="jhnt" * 16,
            source_type="nse_json_hint",
            data_category="fact",
        )
    )
    return doc


class TestRecordHistoricalFinancialResult:
    async def test_creates_verified_row_with_real_available_at(
        self, db_session, company, primary_document
    ) -> None:
        outcome = await record_historical_financial_result(
            db_session,
            company_id=company.id,
            symbol=company.symbol,
            fiscal_year=2022,
            quarter="Q3",
            period_end=date(2021, 12, 31),
            period_start=date(2021, 10, 1),
            period_label="Q3FY22",
            statement_scope="STANDALONE",
            reporting_basis="QUARTER",
            is_audited=True,
            result_date=date(2022, 2, 10),
            unit_scale="LAKH",
            source_document_id=primary_document.id,
            source_page=12,
            source_quote="Profit for the period 89,330",
            revenue=Decimal("435984.00"),
            pbt=Decimal("117226.00"),
            pat=Decimal("89330.00"),
            eps_basic=Decimal("1.22"),
            eps_diluted=Decimal("1.22"),
            tax_expense=Decimal("27896.00"),
            changes_in_inventories=Decimal("-22372.00"),
        )
        await db_session.flush()

        row = outcome.row
        assert outcome.was_new_version is True
        assert row.verification_status == "verified"
        assert row.verification_method == "manual_primary_document"
        assert row.verification_document_id == primary_document.id
        assert row.source_document_id == primary_document.id
        assert row.source_type == "nse_filing_pdf"
        # PIT discipline: available_at must be the document's real historical
        # publication time, never our own ingestion time.
        assert row.available_at == datetime(2022, 2, 10, 14, 25, 42, tzinfo=UTC)
        assert row.timestamp_precision == "EXACT"
        assert row.available_at.year != datetime.now(UTC).year
        assert row.other_metrics["extraction_method"] == "pdf_manual"
        assert row.other_metrics["source_page"] == 12
        assert row.other_metrics["tax_expense"] == "27896.00"
        assert row.other_metrics["changes_in_inventories"] == "-22372.00"

    async def test_idempotent_rerun_does_not_duplicate(
        self, db_session, company, primary_document
    ) -> None:
        kwargs = dict(
            company_id=company.id,
            symbol=company.symbol,
            fiscal_year=2022,
            quarter="Q3",
            period_end=date(2021, 12, 31),
            period_start=date(2021, 10, 1),
            period_label="Q3FY22",
            statement_scope="STANDALONE",
            reporting_basis="QUARTER",
            is_audited=True,
            result_date=date(2022, 2, 10),
            unit_scale="LAKH",
            source_document_id=primary_document.id,
            source_page=12,
            source_quote=None,
            revenue=Decimal("435984.00"),
            pbt=Decimal("117226.00"),
            pat=Decimal("89330.00"),
        )
        first = await record_historical_financial_result(db_session, **kwargs)
        await db_session.flush()
        second = await record_historical_financial_result(db_session, **kwargs)
        await db_session.flush()

        assert first.was_new_version is True
        assert second.was_new_version is False
        assert second.row.id == first.row.id
        assert second.row.version == 1

    async def test_rejects_json_hint_document(
        self, db_session, company, json_hint_document
    ) -> None:
        with pytest.raises(HistoricalFinancialTranscriptionError, match="undocumented JSON hint"):
            await record_historical_financial_result(
                db_session,
                company_id=company.id,
                symbol=company.symbol,
                fiscal_year=2022,
                quarter="Q3",
                period_end=date(2021, 12, 31),
                period_start=None,
                period_label="Q3FY22",
                statement_scope="STANDALONE",
                reporting_basis="QUARTER",
                is_audited=True,
                result_date=None,
                unit_scale="LAKH",
                source_document_id=json_hint_document.id,
                source_page=None,
                source_quote=None,
                revenue=Decimal("435984.00"),
            )

    async def test_rejects_document_from_a_different_company(self, db_session, primary_document) -> None:
        from investing_agent.db.repositories.company import CompanyRepository

        other_company = await CompanyRepository(db_session).upsert(
            CompanyCreate(symbol=f"HAL-{uuid.uuid4().hex[:8]}", name="Hindustan Aeronautics", exchange="NSE")
        )
        with pytest.raises(HistoricalFinancialTranscriptionError, match="different company"):
            await record_historical_financial_result(
                db_session,
                company_id=other_company.id,
                symbol=other_company.symbol,
                fiscal_year=2022,
                quarter="Q3",
                period_end=date(2021, 12, 31),
                period_start=None,
                period_label="Q3FY22",
                statement_scope="STANDALONE",
                reporting_basis="QUARTER",
                is_audited=True,
                result_date=None,
                unit_scale="LAKH",
                source_document_id=primary_document.id,
                source_page=None,
                source_quote=None,
                revenue=Decimal("100.00"),
            )
