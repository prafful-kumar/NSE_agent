from __future__ import annotations

"""Integration tests for the Phase 3A company-intelligence repositories:
source_documents, financial_results, corporate_actions.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d

These are the tests that can only be verified against a real database:
versioned upsert idempotency, corrected-filing version preservation,
point-in-time (available_at <= as_of) queries, and future-data-leakage
protection. Ingestion *orchestration* (discover -> archive -> normalize ->
verify) is covered with mocked repos in tests/unit/test_ingestion_services.py;
this file proves the persistence contracts those services rely on.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.company_research import (
    ManagementGuidanceCreate,
    OrderBookSnapshotCreate,
)
from investing_agent.schemas.corporate_actions import CorporateActionCreate
from investing_agent.schemas.financials import FinancialResultCreate
from investing_agent.schemas.source_documents import SourceDocumentCreate

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
async def bel(db_session):
    from investing_agent.db.repositories.company import CompanyRepository

    repo = CompanyRepository(db_session)
    return await repo.upsert(
        CompanyCreate(
            symbol=f"BEL-{uuid.uuid4().hex[:8]}", name="Bharat Electronics", exchange="NSE"
        )
    )


@pytest.fixture
async def source_doc(db_session, bel):
    """An archived document to cite as source_document_id — every Phase 3B
    ExtractionMixin row requires one (NOT NULL FK)."""
    from investing_agent.db.repositories.source_document import SourceDocumentRepository

    repo = SourceDocumentRepository(db_session)
    doc, _ = await repo.get_or_create(
        SourceDocumentCreate(
            company_id=bel.id, symbol=bel.symbol, exchange="IR",
            filing_type="investor_presentation", document_type="pdf",
            title="Q1FY27 Investor Presentation", content_hash="doc1" * 16,
            source_type="manual_upload", source_url="file:///tmp/ip.pdf",
            data_category="fact",
        )
    )
    return doc


class TestSourceDocumentRepositoryIdempotency:
    async def test_get_or_create_is_idempotent_on_same_content(self, db_session, bel) -> None:
        from investing_agent.db.repositories.source_document import SourceDocumentRepository

        repo = SourceDocumentRepository(db_session)
        create = SourceDocumentCreate(
            company_id=bel.id, symbol=bel.symbol, exchange="NSE",
            filing_type="quarterly_result", document_type="json",
            title="NSE quarterly results hint", content_hash="deadbeef" * 8,
            source_type="nse_json_hint", source_url="https://nse/results", data_category="fact",
        )

        first, first_created = await repo.get_or_create(create)
        second, second_created = await repo.get_or_create(create)
        await db_session.flush()

        assert first_created is True
        assert second_created is False
        assert first.id == second.id

    async def test_different_content_hash_creates_new_document(self, db_session, bel) -> None:
        from investing_agent.db.repositories.source_document import SourceDocumentRepository

        repo = SourceDocumentRepository(db_session)
        base = dict(
            company_id=bel.id, symbol=bel.symbol, exchange="NSE",
            filing_type="quarterly_result", document_type="json",
            title="hint", source_type="nse_json_hint",
            source_url="https://nse/results", data_category="fact",
        )
        first, _ = await repo.get_or_create(SourceDocumentCreate(**base, content_hash="a" * 64))
        second, created = await repo.get_or_create(
            SourceDocumentCreate(**base, content_hash="b" * 64)
        )
        await db_session.flush()

        assert created is True
        assert first.id != second.id

    async def test_record_amendment_chains_versions(self, db_session, bel) -> None:
        from investing_agent.db.repositories.source_document import SourceDocumentRepository

        repo = SourceDocumentRepository(db_session)
        base = dict(
            company_id=bel.id, symbol=bel.symbol, exchange="NSE",
            filing_type="quarterly_result", document_type="pdf",
            title="Q3 result", source_type="nse_filing_pdf",
            source_url="https://nse/filing", data_category="fact",
        )
        original, _ = await repo.get_or_create(
            SourceDocumentCreate(**base, content_hash="v1" * 32)
        )
        amended, _ = await repo.get_or_create(
            SourceDocumentCreate(**base, content_hash="v2" * 32)
        )

        version = await repo.record_amendment(original, amended, notes="revised PAT")
        await db_session.flush()

        assert version.version_number == 2
        assert version.status == "amended"
        assert version.source_document_id == amended.id


class TestFinancialResultRepositoryVersioning:
    def _create(
        self, *, company_id, period_id, pat: str, source_type="nse_json_hint"
    ) -> FinancialResultCreate:
        return FinancialResultCreate(
            period_id=period_id, company_id=company_id, symbol="BEL",
            statement_scope="STANDALONE", reporting_basis="QUARTER",
            is_audited=False, result_date=date(2025, 1, 30), currency="INR",
            unit_scale="LAKH", revenue=Decimal("575612"), pbt=Decimal("175415"),
            pat=Decimal(pat), eps_basic=Decimal("1.81"),
            source_type=source_type, source_url="https://nse/results",
            data_category="fact",
        )

    async def _period(self, db_session, bel):
        from investing_agent.db.repositories.financial import FinancialPeriodRepository

        repo = FinancialPeriodRepository(db_session)
        return await repo.get_or_create(
            company_id=bel.id, period_type="quarter", fiscal_year=2025,
            quarter="Q3", period_end=date(2024, 12, 31),
            period_start=date(2024, 10, 1), label="Q3FY25",
        )

    async def test_identical_reingestion_is_idempotent_no_new_version(
        self, db_session, bel
    ) -> None:
        from investing_agent.db.repositories.financial import FinancialResultRepository

        period = await self._period(db_session, bel)
        repo = FinancialResultRepository(db_session)

        row1, was_new1 = await repo.upsert_versioned(
            self._create(company_id=bel.id, period_id=period.id, pat="131606")
        )
        row2, was_new2 = await repo.upsert_versioned(
            self._create(company_id=bel.id, period_id=period.id, pat="131606")
        )
        await db_session.flush()

        assert was_new1 is True
        assert was_new2 is False
        assert row1.id == row2.id
        assert row1.version == 1

    async def test_revised_filing_creates_new_version_and_preserves_prior(
        self, db_session, bel
    ) -> None:
        from investing_agent.db.repositories.financial import FinancialResultRepository

        period = await self._period(db_session, bel)
        repo = FinancialResultRepository(db_session)

        original, _ = await repo.upsert_versioned(
            self._create(company_id=bel.id, period_id=period.id, pat="131606")
        )
        revised, was_new = await repo.upsert_versioned(
            self._create(company_id=bel.id, period_id=period.id, pat="135000")
        )
        await db_session.flush()
        await db_session.refresh(original)

        assert was_new is True
        assert revised.id != original.id
        assert revised.version == 2
        assert revised.supersedes_id == original.id
        assert revised.is_latest is True
        assert original.is_latest is False
        assert original.pat == Decimal("131606")  # prior version untouched, not overwritten

    async def test_point_in_time_query_excludes_future_information(self, db_session, bel) -> None:
        """The core anti-look-ahead-bias guarantee: as_of before a version's
        available_at must never surface that version's values.

        available_at is set explicitly rather than relying on wall-clock
        ordering across two flushes: PostgreSQL's func.now() is
        transaction-scoped (== CURRENT_TIMESTAMP), so two inserts flushed
        within the same uncommitted test transaction would otherwise receive
        an identical timestamp and make this test's ordering non-deterministic.
        """
        from investing_agent.db.repositories.financial import FinancialResultRepository

        period = await self._period(db_session, bel)
        repo = FinancialResultRepository(db_session)

        original_available_at = datetime(2025, 1, 30, 10, 0, tzinfo=UTC)
        revised_available_at = datetime(2025, 2, 15, 9, 0, tzinfo=UTC)

        original, _ = await repo.upsert_versioned(
            self._create(company_id=bel.id, period_id=period.id, pat="131606")
        )
        original.available_at = original_available_at
        await db_session.flush()

        revised, _ = await repo.upsert_versioned(
            self._create(company_id=bel.id, period_id=period.id, pat="999999")
        )
        revised.available_at = revised_available_at
        await db_session.flush()

        cutoff = datetime(2025, 2, 1, tzinfo=UTC)  # strictly between the two

        as_of_row = await repo.get_as_of(
            period.id, "STANDALONE", "QUARTER", "nse_json_hint", as_of=cutoff
        )
        assert as_of_row is not None
        assert as_of_row.id == original.id
        assert as_of_row.pat == Decimal("131606")  # never leaks the not-yet-available revision

        before_any_data = await repo.get_as_of(
            period.id, "STANDALONE", "QUARTER", "nse_json_hint",
            as_of=datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert before_any_data is None  # nothing was known yet

        latest = await repo.get_as_of(
            period.id, "STANDALONE", "QUARTER", "nse_json_hint",
            as_of=datetime(2025, 3, 1, tzinfo=UTC),
        )
        assert latest.id == revised.id
        assert latest.pat == Decimal("999999")

    async def test_get_history_preserves_all_versions_oldest_first(self, db_session, bel) -> None:
        from investing_agent.db.repositories.financial import FinancialResultRepository

        period = await self._period(db_session, bel)
        repo = FinancialResultRepository(db_session)

        await repo.upsert_versioned(
            self._create(company_id=bel.id, period_id=period.id, pat="131606")
        )
        await repo.upsert_versioned(
            self._create(company_id=bel.id, period_id=period.id, pat="140000")
        )
        await repo.upsert_versioned(
            self._create(company_id=bel.id, period_id=period.id, pat="145000")
        )
        await db_session.flush()

        history = await repo.get_history(period.id, "STANDALONE", "QUARTER", "nse_json_hint")
        assert [h.version for h in history] == [1, 2, 3]
        assert [h.pat for h in history] == [Decimal("131606"), Decimal("140000"), Decimal("145000")]

    async def test_duplicate_filing_discovered_twice_does_not_double_count(
        self, db_session, bel
    ) -> None:
        """Same filing discovered twice (e.g. re-run of sync-financial-results)
        must not create a spurious extra version."""
        from investing_agent.db.repositories.financial import FinancialResultRepository

        period = await self._period(db_session, bel)
        repo = FinancialResultRepository(db_session)

        for _ in range(3):
            await repo.upsert_versioned(
                self._create(company_id=bel.id, period_id=period.id, pat="131606")
            )
        await db_session.flush()

        history = await repo.get_history(period.id, "STANDALONE", "QUARTER", "nse_json_hint")
        assert len(history) == 1


class TestCorporateActionRepositoryVersioningAndCalendar:
    def _dividend(
        self, *, company_id, ex_date: date, amount: str, source_type="nse_json_hint"
    ) -> CorporateActionCreate:
        return CorporateActionCreate(
            company_id=company_id, symbol="BEL", action_type="dividend",
            event_date=ex_date, ex_date=ex_date, record_date=ex_date,
            amount=Decimal(amount), source_type=source_type, data_category="fact",
        )

    async def test_amended_corporate_action_creates_new_version(self, db_session, bel) -> None:
        """A postponed/revised dividend (same event_date+action_type+source,
        different amount or dates) must version, not overwrite."""
        from investing_agent.db.repositories.corporate_action import CorporateActionRepository

        repo = CorporateActionRepository(db_session)
        ex_date = date(2026, 8, 13)

        original, was_new1 = await repo.upsert_versioned(
            self._dividend(company_id=bel.id, ex_date=ex_date, amount="0.55")
        )
        amended, was_new2 = await repo.upsert_versioned(
            self._dividend(company_id=bel.id, ex_date=ex_date, amount="0.65")
        )
        await db_session.flush()
        await db_session.refresh(original)

        assert was_new1 is True
        assert was_new2 is True
        assert amended.version == 2
        assert amended.supersedes_id == original.id
        assert amended.is_latest is True
        assert original.is_latest is False
        assert original.amount == Decimal("0.55")

    async def test_board_meeting_postponement_versions_expected_result_date(
        self, db_session, bel
    ) -> None:
        from investing_agent.db.repositories.corporate_action import CorporateActionRepository

        repo = CorporateActionRepository(db_session)
        announced_date = date(2025, 1, 20)

        first = CorporateActionCreate(
            company_id=bel.id, symbol="BEL", action_type="board_meeting",
            event_date=announced_date, expected_result_date=date(2025, 1, 30),
            source_type="nse_json_hint", data_category="fact",
        )
        postponed = CorporateActionCreate(
            company_id=bel.id, symbol="BEL", action_type="board_meeting",
            event_date=announced_date, expected_result_date=date(2025, 2, 5),
            source_type="nse_json_hint", data_category="fact",
        )

        row1, _ = await repo.upsert_versioned(first)
        row2, was_new = await repo.upsert_versioned(postponed)
        await db_session.flush()

        assert was_new is True
        assert row2.version == 2
        assert row2.expected_result_date == date(2025, 2, 5)

    async def test_dividend_without_payment_date_persists_as_none(self, db_session, bel) -> None:
        from investing_agent.db.repositories.corporate_action import CorporateActionRepository

        repo = CorporateActionRepository(db_session)
        row, _ = await repo.upsert_versioned(
            self._dividend(company_id=bel.id, ex_date=date(2026, 8, 13), amount="0.55")
        )
        await db_session.flush()

        assert row.payment_date is None

    async def test_dividend_calendar_point_in_time(self, db_session, bel) -> None:
        """A dividend ingested "in the future" relative to as_of must not
        appear in a calendar query with an earlier as_of — no look-ahead.
        available_at is set explicitly for the same reason as the financial-
        results PIT test: func.now() is transaction-scoped in PostgreSQL."""
        from investing_agent.db.repositories.corporate_action import CorporateActionRepository

        repo = CorporateActionRepository(db_session)
        # Anchored to real "today" (not a fixed date) so the ex_date stays
        # inside the calendar's [today, today+days] window regardless of
        # when this test runs.
        ingested_at = datetime.combine(date.today(), datetime.min.time(), tzinfo=UTC)

        future_ex_date = date.today() + timedelta(days=10)
        row, _ = await repo.upsert_versioned(
            self._dividend(company_id=bel.id, ex_date=future_ex_date, amount="0.55")
        )
        row.available_at = ingested_at
        await db_session.flush()

        before = await repo.get_dividend_calendar(
            company_ids=[bel.id], days=30, as_of=ingested_at - timedelta(hours=1)
        )
        after = await repo.get_dividend_calendar(
            company_ids=[bel.id], days=30, as_of=ingested_at + timedelta(hours=1)
        )

        assert before == []
        assert len(after) == 1
        assert after[0].ex_date == future_ex_date

    async def test_list_by_company_as_of_excludes_future_amendment(self, db_session, bel) -> None:
        """available_at is set explicitly (see test_point_in_time_query_
        excludes_future_information for why: func.now() is transaction-scoped
        in PostgreSQL, so two flushes in the same open transaction can't be
        relied on to produce distinct timestamps)."""
        from investing_agent.db.repositories.corporate_action import CorporateActionRepository

        repo = CorporateActionRepository(db_session)
        ex_date = date(2026, 8, 13)

        original, _ = await repo.upsert_versioned(
            self._dividend(company_id=bel.id, ex_date=ex_date, amount="0.55")
        )
        original.available_at = datetime(2026, 7, 1, tzinfo=UTC)
        await db_session.flush()

        amended, _ = await repo.upsert_versioned(
            self._dividend(company_id=bel.id, ex_date=ex_date, amount="0.65")
        )
        amended.available_at = datetime(2026, 7, 20, tzinfo=UTC)
        await db_session.flush()

        cutoff = datetime(2026, 7, 10, tzinfo=UTC)  # strictly between the two

        as_of_rows = await repo.list_by_company_as_of(bel.id, cutoff)
        assert len(as_of_rows) == 1
        assert as_of_rows[0].amount == Decimal("0.55")

        latest_rows = await repo.list_by_company_as_of(bel.id, datetime(2026, 8, 1, tzinfo=UTC))
        assert len(latest_rows) == 1
        assert latest_rows[0].amount == Decimal("0.65")


class TestOrderBookSnapshotRepository:
    """Phase 3B: unlike Phase 3A's versioned repos above, these are plain
    inserts (no is_latest/supersedes_id) — each row is a distinct
    human-transcribed observation, not a correction of a prior row."""

    def _snapshot(self, *, company_id, source_document_id, value: str) -> OrderBookSnapshotCreate:
        return OrderBookSnapshotCreate(
            company_id=company_id, symbol="BEL", as_of_date=date(2026, 6, 30),
            order_book_value=Decimal(value), currency="INR", unit_scale="CRORE",
            segment="Defence", expected_execution_period="3-4 years",
            source_document_id=source_document_id, source_page=12,
            source_quote="Order book stood at the disclosed value as of Q1FY27.",
            source_type="manual_entry",
        )

    async def test_create_and_list_by_company(self, db_session, bel, source_doc) -> None:
        from investing_agent.db.repositories.company_research import (
            OrderBookSnapshotRepository,
        )

        repo = OrderBookSnapshotRepository(db_session)
        created = await repo.create(
            self._snapshot(company_id=bel.id, source_document_id=source_doc.id, value="75000")
        )
        await db_session.flush()

        assert created.verification_status == "UNVERIFIED"  # never auto-verified
        assert created.extraction_method == "MANUAL"
        assert created.source_document_id == source_doc.id

        rows = await repo.list_by_company(bel.id)
        assert len(rows) == 1
        assert rows[0].id == created.id

    async def test_point_in_time_excludes_future_snapshot(
        self, db_session, bel, source_doc
    ) -> None:
        from investing_agent.db.repositories.company_research import (
            OrderBookSnapshotRepository,
        )

        repo = OrderBookSnapshotRepository(db_session)
        row = await repo.create(
            self._snapshot(company_id=bel.id, source_document_id=source_doc.id, value="75000")
        )
        row.available_at = datetime(2026, 7, 15, tzinfo=UTC)
        await db_session.flush()

        before = await repo.list_by_company_as_of(bel.id, datetime(2026, 7, 1, tzinfo=UTC))
        after = await repo.list_by_company_as_of(bel.id, datetime(2026, 8, 1, tzinfo=UTC))

        assert before == []
        assert len(after) == 1
        assert after[0].id == row.id

    async def test_two_snapshots_are_independent_rows_not_versioned(
        self, db_session, bel, source_doc
    ) -> None:
        """No version-chain machinery: a second snapshot for a different
        as_of_date is just another row, not a correction of the first."""
        from investing_agent.db.repositories.company_research import (
            OrderBookSnapshotRepository,
        )

        repo = OrderBookSnapshotRepository(db_session)
        await repo.create(
            self._snapshot(company_id=bel.id, source_document_id=source_doc.id, value="75000")
        )
        await repo.create(
            self._snapshot(company_id=bel.id, source_document_id=source_doc.id, value="82000")
        )
        await db_session.flush()

        rows = await repo.list_by_company(bel.id)
        assert len(rows) == 2
        assert {r.order_book_value for r in rows} == {Decimal("75000"), Decimal("82000")}


class TestManagementGuidanceRepository:
    def _guidance(
        self, *, company_id, source_document_id, low: str, high: str
    ) -> ManagementGuidanceCreate:
        return ManagementGuidanceCreate(
            company_id=company_id, symbol="BEL", fiscal_year=2027,
            guidance_type="revenue", metric_label="Revenue growth",
            guidance_value_text=f"{low}-{high}% revenue growth",
            guidance_low=Decimal(low), guidance_high=Decimal(high),
            period_label="FY27", given_by="CMD",
            source_document_id=source_document_id, source_page=5,
            source_quote="We expect double-digit revenue growth in FY27.",
            source_type="manual_entry",
        )

    async def test_create_defaults_to_manual_unverified(self, db_session, bel, source_doc) -> None:
        from investing_agent.db.repositories.company_research import (
            ManagementGuidanceRepository,
        )

        repo = ManagementGuidanceRepository(db_session)
        row = await repo.create(
            self._guidance(company_id=bel.id, source_document_id=source_doc.id, low="15", high="18")
        )
        await db_session.flush()

        assert row.extraction_method == "MANUAL"
        assert row.verification_status == "UNVERIFIED"
        assert row.guidance_value_text == "15-18% revenue growth"

    async def test_verify_flag_sets_human_verified(self, db_session, bel, source_doc) -> None:
        """Mirrors the CLI's --verify path: verification_status only flips
        on an explicit human action, never automatically at ingestion."""
        from investing_agent.db.repositories.company_research import (
            ManagementGuidanceRepository,
        )
        from investing_agent.schemas.company_research import ManagementGuidanceCreate

        repo = ManagementGuidanceRepository(db_session)
        data = ManagementGuidanceCreate(
            company_id=bel.id, symbol="BEL", fiscal_year=2027,
            guidance_type="revenue", metric_label="Revenue growth",
            guidance_value_text="15-18% revenue growth",
            source_document_id=source_doc.id, source_type="manual_entry",
            verification_status="HUMAN_VERIFIED", verified_by="analyst@example.com",
            verified_at=datetime(2026, 8, 18, tzinfo=UTC),
        )
        row = await repo.create(data)
        await db_session.flush()

        assert row.verification_status == "HUMAN_VERIFIED"
        assert row.verified_by == "analyst@example.com"

    async def test_list_by_company_as_of_point_in_time(self, db_session, bel, source_doc) -> None:
        from investing_agent.db.repositories.company_research import (
            ManagementGuidanceRepository,
        )

        repo = ManagementGuidanceRepository(db_session)
        row = await repo.create(
            self._guidance(company_id=bel.id, source_document_id=source_doc.id, low="15", high="18")
        )
        row.available_at = datetime(2026, 7, 15, tzinfo=UTC)
        await db_session.flush()

        before = await repo.list_by_company_as_of(bel.id, datetime(2026, 7, 1, tzinfo=UTC))
        after = await repo.list_by_company_as_of(bel.id, datetime(2026, 8, 1, tzinfo=UTC))

        assert before == []
        assert len(after) == 1
        assert after[0].id == row.id
