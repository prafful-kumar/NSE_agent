from __future__ import annotations

"""Unit tests for CorporateActionIngestionService / FinancialResultIngestionService.

These exercise the real discover -> archive -> normalize -> verify pipeline
using the BEL/HAL fixtures captured during the Phase 3 bake-off
(tests/fixtures/nse/*.json via FakeNSEDataSource) so normalization and
cross-source verification run for real. Only persistence (the repositories,
which require PostgreSQL) is mocked — repo-level versioning/idempotency/PIT
behavior is covered separately in tests/integration/.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from investing_agent.services.ingestion.corporate_actions import (
    CorporateActionIngestionService,
)
from investing_agent.services.ingestion.financial_results import (
    FinancialResultIngestionService,
)
from investing_agent.services.normalization import (
    CorporateActionNormalizer,
    FinancialResultNormalizer,
)
from investing_agent.services.sources.interfaces import (
    CorporateActionSource,
    DiscoveredDocument,
    RawCorporateAction,
    RawFinancialResult,
)
from tests.fixtures.fake_nse_source import FakeNSEDataSource


def _company(symbol: str = "BEL") -> MagicMock:
    c = MagicMock()
    c.id = uuid.uuid4()
    c.symbol = symbol
    return c


async def _fake_archive(session, company, doc):
    """Stand-in for services.ingestion.common.archive_document: always
    "creates" a new document when one is offered, mirroring get_or_create's
    contract without touching a real DB."""
    if doc is None:
        return None, False
    row = MagicMock()
    row.id = uuid.uuid4()
    return row, True


class _EmptyBSE(CorporateActionSource):
    """Mirrors the real BSEDataSource today: no verified access, honestly
    returns nothing rather than fabricate data."""

    async def aclose(self) -> None:
        pass

    async def get_dividends(self, symbol: str):
        return [], None

    async def get_board_meetings(self, symbol: str):
        return [], None

    async def get_corporate_actions(self, symbol: str):
        return [], None


class _MatchingBSE(CorporateActionSource):
    """A BSE source that independently confirms exactly one BEL dividend
    (ex_date=2026-08-13, amount=0.55), to exercise cross-source verification."""

    async def aclose(self) -> None:
        pass

    async def get_dividends(self, symbol: str):
        actions, doc = await self.get_corporate_actions(symbol)
        return [a for a in actions if a.action_type == "dividend"], doc

    async def get_board_meetings(self, symbol: str):
        return [], None

    async def get_corporate_actions(self, symbol: str):
        row = RawCorporateAction(
            action_type="dividend", announced_date=None, event_date="13-Aug-2026",
            ex_date="13-Aug-2026", record_date="13-Aug-2026", payment_date=None,
            agm_date=None, amount_text="Dividend - Re 0.55 Per Share", dividend_type=None,
            board_meeting_announced_at=None, board_meeting_date=None,
            expected_result_date=None, actual_result_published_at=None, raw={},
        )
        doc = DiscoveredDocument(
            company_symbol=symbol, exchange="BSE", source="BSE",
            source_type="bse_json_hint", filing_type="announcement",
            document_type="json", title="BSE corporate actions hint",
            content=b"[]", source_url="https://api.bseindia.com/corpactions",
            fetched_at=datetime.now(UTC),
        )
        return [row], doc


def _ca_service(nse=None, bse=None) -> CorporateActionIngestionService:
    svc = CorporateActionIngestionService.__new__(CorporateActionIngestionService)
    svc._session = AsyncMock()
    svc._nse = nse or FakeNSEDataSource()
    svc._bse = bse or _EmptyBSE()
    svc._action_repo = AsyncMock()

    async def _upsert(data):
        row = MagicMock(**data.model_dump())
        return row, True

    svc._action_repo.upsert_versioned = AsyncMock(side_effect=_upsert)
    svc._normalizer = CorporateActionNormalizer()
    return svc


def _fin_service(nse=None) -> FinancialResultIngestionService:
    svc = FinancialResultIngestionService.__new__(FinancialResultIngestionService)
    svc._session = AsyncMock()
    svc._nse = nse or FakeNSEDataSource()
    svc._period_repo = AsyncMock()

    async def _get_or_create(**kwargs):
        period = MagicMock()
        period.id = uuid.uuid4()
        return period

    svc._period_repo.get_or_create = AsyncMock(side_effect=_get_or_create)
    svc._result_repo = AsyncMock()

    async def _upsert(data):
        row = MagicMock(**data.model_dump())
        return row, True

    svc._result_repo.upsert_versioned = AsyncMock(side_effect=_upsert)
    svc._normalizer = FinancialResultNormalizer()
    return svc


class TestCorporateActionIngestionService:
    @pytest.mark.asyncio
    async def test_bel_dividends_ingest_end_to_end_stay_unverified_without_bse(self) -> None:
        svc = _ca_service()
        company = _company("BEL")
        with (
            patch(
                "investing_agent.services.ingestion.corporate_actions.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.services.ingestion.corporate_actions.archive_document",
                AsyncMock(side_effect=_fake_archive),
            ),
        ):
            result = await svc.sync("bel")

        assert result.symbol == "BEL"
        assert len(result.rows) >= 5  # BEL fixture has several dividend rows
        assert result.documents_archived == 1  # only NSE returned a document (BSE returned none)
        assert result.verified_count == 0
        assert all(row.verification_status == "unverified" for row in result.rows)
        # payment_date is never inferred from NSE's corporate-actions feed
        assert all(row.payment_date is None for row in result.rows)

    @pytest.mark.asyncio
    async def test_matching_bse_source_flips_one_row_to_verified(self) -> None:
        svc = _ca_service(bse=_MatchingBSE())
        company = _company("BEL")
        with (
            patch(
                "investing_agent.services.ingestion.corporate_actions.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.services.ingestion.corporate_actions.archive_document",
                AsyncMock(side_effect=_fake_archive),
            ),
        ):
            result = await svc.sync("BEL")

        assert result.documents_archived == 2  # NSE + BSE both returned a document
        assert result.verified_count == 1
        verified_rows = [r for r in result.rows if r.verification_status == "verified"]
        assert len(verified_rows) == 1
        assert verified_rows[0].verification_method == "cross_source"
        assert verified_rows[0].amount == 0.55 or str(verified_rows[0].amount) == "0.55"

    @pytest.mark.asyncio
    async def test_hal_dividends_ingest(self) -> None:
        svc = _ca_service()
        company = _company("HAL")
        with (
            patch(
                "investing_agent.services.ingestion.corporate_actions.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.services.ingestion.corporate_actions.archive_document",
                AsyncMock(side_effect=_fake_archive),
            ),
        ):
            result = await svc.sync("HAL")

        assert result.symbol == "HAL"
        assert len(result.rows) >= 1

    @pytest.mark.asyncio
    async def test_missing_symbol_has_no_document_and_no_rows(self) -> None:
        svc = _ca_service()
        company = _company("ZZZZ")
        with (
            patch(
                "investing_agent.services.ingestion.corporate_actions.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.services.ingestion.corporate_actions.archive_document",
                AsyncMock(side_effect=_fake_archive),
            ),
        ):
            result = await svc.sync("ZZZZ")

        assert result.rows == []
        assert result.documents_archived == 0
        assert result.skipped_unparseable == 0

    @pytest.mark.asyncio
    async def test_row_with_unparseable_event_date_is_skipped_not_guessed(self) -> None:
        class _BadRowNSE(CorporateActionSource):
            async def aclose(self) -> None:
                pass

            async def get_dividends(self, symbol: str):
                return await self.get_corporate_actions(symbol)

            async def get_board_meetings(self, symbol: str):
                return [], None

            async def get_corporate_actions(self, symbol: str):
                row = RawCorporateAction(
                    action_type="dividend", announced_date=None, event_date=None,
                    ex_date=None, record_date=None, payment_date=None, agm_date=None,
                    amount_text="Dividend - Rs 1 Per Share", dividend_type=None,
                    board_meeting_announced_at=None, board_meeting_date=None,
                    expected_result_date=None, actual_result_published_at=None, raw={},
                )
                doc = DiscoveredDocument(
                    company_symbol=symbol, exchange="NSE", source="NSE",
                    source_type="nse_json_hint", filing_type="announcement",
                    document_type="json", title="hint", content=b"[]",
                    source_url="https://x", fetched_at=datetime.now(UTC),
                )
                return [row], doc

        svc = _ca_service(nse=_BadRowNSE())
        company = _company("BEL")
        with (
            patch(
                "investing_agent.services.ingestion.corporate_actions.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.services.ingestion.corporate_actions.archive_document",
                AsyncMock(side_effect=_fake_archive),
            ),
        ):
            result = await svc.sync("BEL")

        assert result.rows == []
        assert result.skipped_unparseable == 1
        assert result.documents_archived == 1  # the document itself is still archived


class TestFinancialResultIngestionService:
    @pytest.mark.asyncio
    async def test_bel_quarterly_results_ingest_end_to_end(self) -> None:
        svc = _fin_service()
        company = _company("BEL")
        with (
            patch(
                "investing_agent.services.ingestion.financial_results.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.services.ingestion.financial_results.archive_document",
                AsyncMock(side_effect=_fake_archive),
            ),
        ):
            result = await svc.sync("bel")

        assert result.symbol == "BEL"
        assert len(result.rows) >= 1
        assert result.documents_archived == 1

        # Regression: the misleading re_con_pro_loss field name must never
        # cause statement_scope to read as CONSOLIDATED end-to-end through
        # the ingestion pipeline (see test_financial_normalization.py for the
        # unit-level version of this same guarantee).
        q3fy25 = next(
            r for r in result.rows
            if r.result_date and r.result_date.isoformat() == "2025-01-30"
        )
        assert q3fy25.statement_scope == "STANDALONE"
        assert q3fy25.pat == 131606
        assert q3fy25.verification_status == "unverified"

    @pytest.mark.asyncio
    async def test_hal_quarterly_results_ingest(self) -> None:
        svc = _fin_service()
        company = _company("HAL")
        with (
            patch(
                "investing_agent.services.ingestion.financial_results.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.services.ingestion.financial_results.archive_document",
                AsyncMock(side_effect=_fake_archive),
            ),
        ):
            result = await svc.sync("HAL")

        assert result.symbol == "HAL"
        assert len(result.rows) >= 1

    @pytest.mark.asyncio
    async def test_row_with_unparseable_period_end_is_skipped_not_guessed(self) -> None:
        from investing_agent.services.sources.interfaces import FinancialResultSource

        class _BadPeriodNSE(FinancialResultSource):
            async def aclose(self) -> None:
                pass

            async def get_annual_results(self, symbol: str):
                return [], None

            async def get_quarterly_results(self, symbol: str):
                raw = RawFinancialResult(
                    period_start="01-OCT-2024", period_end="not-a-date", result_date=None,
                    revenue="1", pat="1", pbt="1", eps_basic="1", is_audited_hint=None,
                    statement_scope_hint=None, unit_scale_hint=None,
                    extraction_method="structured_api", raw={},
                )
                doc = DiscoveredDocument(
                    company_symbol=symbol, exchange="NSE", source="NSE",
                    source_type="nse_json_hint", filing_type="quarterly_result",
                    document_type="json", title="hint", content=b"{}",
                    source_url="https://x", fetched_at=datetime.now(UTC),
                )
                return [raw], doc

        svc = _fin_service(nse=_BadPeriodNSE())
        company = _company("BEL")
        with (
            patch(
                "investing_agent.services.ingestion.financial_results.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.services.ingestion.financial_results.archive_document",
                AsyncMock(side_effect=_fake_archive),
            ),
        ):
            result = await svc.sync("BEL")

        assert result.rows == []
        assert result.skipped_unparseable == 1
        assert result.documents_archived == 1

    @pytest.mark.asyncio
    async def test_missing_symbol_has_no_document_and_no_rows(self) -> None:
        svc = _fin_service()
        company = _company("ZZZZ")
        with (
            patch(
                "investing_agent.services.ingestion.financial_results.ensure_company",
                AsyncMock(return_value=company),
            ),
            patch(
                "investing_agent.services.ingestion.financial_results.archive_document",
                AsyncMock(side_effect=_fake_archive),
            ),
        ):
            result = await svc.sync("ZZZZ")

        assert result.rows == []
        assert result.documents_archived == 0
