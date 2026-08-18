from __future__ import annotations

"""Unit tests for the factual_company_context LangGraph node.

Core rule under test: this node reads only from PostgreSQL (via repositories)
and never talks to NSE/BSE directly. Every fact it returns must carry
verification_status and source_document_id so downstream nodes/UI can tell a
cross-checked FACT apart from an unverified JSON hint.

Phase 3A facts (financials/corporate_actions) use VerificationMixin's
lowercase "unverified"/"verified" vocabulary; Phase 3B facts (order book,
guidance, segment/operational metrics, capacity updates, commentary) use
ExtractionMixin's UNVERIFIED/HUMAN_VERIFIED/DOCUMENT_VERIFIED/REJECTED
vocabulary — deliberately distinct, see factual_company_context.py.
"""

import uuid
from contextlib import ExitStack
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from investing_agent.agents.nodes.factual_company_context import (
    factual_company_context_node,
)

_PHASE3B_REPO_NAMES = [
    "OrderBookSnapshotRepository",
    "ManagementGuidanceRepository",
    "SegmentMetricRepository",
    "OperationalMetricRepository",
    "CapacityUpdateRepository",
    "ManagementCommentaryRepository",
]


def _financial_row() -> MagicMock:
    return MagicMock(
        period_id=uuid.uuid4(),
        statement_scope="STANDALONE",
        reporting_basis="QUARTER",
        result_date=date(2025, 1, 30),
        revenue=Decimal("575612"),
        pbt=Decimal("175415"),
        pat=Decimal("131606"),
        eps_basic=Decimal("1.81"),
        unit_scale="LAKH",
        currency="INR",
        is_audited=False,
        verification_status="unverified",
        source_document_id=uuid.uuid4(),
        source_type="nse_json_hint",
        source_url="https://www.nseindia.com/api/results-comparision?symbol=BEL",
        published_at=datetime(2025, 1, 30, tzinfo=UTC),
        available_at=datetime(2025, 1, 30, 12, tzinfo=UTC),
        data_category="fact",
    )


def _action_row() -> MagicMock:
    return MagicMock(
        action_type="dividend",
        event_date=date(2026, 8, 13),
        ex_date=date(2026, 8, 13),
        record_date=date(2026, 8, 13),
        payment_date=None,
        amount=Decimal("0.55"),
        dividend_type=None,
        verification_status="verified",
        source_document_id=uuid.uuid4(),
        source_type="nse_json_hint",
        source_url="https://www.nseindia.com/api/corporates-corporateActions?symbol=BEL",
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
        available_at=datetime(2026, 8, 1, 9, tzinfo=UTC),
        data_category="fact",
    )


def _order_book_row(verification_status: str = "UNVERIFIED") -> MagicMock:
    return MagicMock(
        as_of_date=date(2026, 6, 30),
        order_book_value=Decimal("75000"),
        currency="INR",
        unit_scale="CRORE",
        segment="Defence",
        book_to_bill_ratio=Decimal("3.5"),
        expected_execution_period="3-4 years",
        verification_status=verification_status,
        source_document_id=uuid.uuid4(),
        source_type="manual_entry",
        source_url=None,
        published_at=None,
        data_category="fact",
        source_quote="Order book stood at ₹75,000 crore as of Q1FY27.",
    )


def _patch_repos(
    company,
    financial_rows: list | None = None,
    action_rows: list | None = None,
    order_book_rows: list | None = None,
) -> ExitStack:
    """Patches every repository the node instantiates. Phase 3B repos default
    to returning [] unless order_book_rows is given (only OrderBookSnapshot
    is exercised with real rows in these tests — the other five Phase 3B
    repos share the exact same code path in the node, see
    _extraction_evidence, so one representative type is enough here)."""
    stack = ExitStack()

    company_repo = AsyncMock()
    company_repo.get_by_symbol = AsyncMock(return_value=company)
    stack.enter_context(patch(
        "investing_agent.agents.nodes.factual_company_context.CompanyRepository",
        return_value=company_repo,
    ))

    financial_repo = AsyncMock()
    financial_repo.list_by_company = AsyncMock(return_value=financial_rows or [])
    stack.enter_context(patch(
        "investing_agent.agents.nodes.factual_company_context.FinancialResultRepository",
        return_value=financial_repo,
    ))

    action_repo = AsyncMock()
    action_repo.list_by_company = AsyncMock(return_value=action_rows or [])
    stack.enter_context(patch(
        "investing_agent.agents.nodes.factual_company_context.CorporateActionRepository",
        return_value=action_repo,
    ))

    order_book_repo = AsyncMock()
    order_book_repo.list_by_company = AsyncMock(return_value=order_book_rows or [])
    stack.enter_context(patch(
        "investing_agent.agents.nodes.factual_company_context.OrderBookSnapshotRepository",
        return_value=order_book_repo,
    ))

    for name in _PHASE3B_REPO_NAMES[1:]:
        empty_repo = AsyncMock()
        empty_repo.list_by_company = AsyncMock(return_value=[])
        stack.enter_context(patch(
            f"investing_agent.agents.nodes.factual_company_context.{name}",
            return_value=empty_repo,
        ))

    return stack


class TestFactualCompanyContextNode:
    @pytest.mark.asyncio
    async def test_serves_facts_and_evidence_from_db(self) -> None:
        company = MagicMock(id=uuid.uuid4(), symbol="BEL")
        fin_row, action_row = _financial_row(), _action_row()

        state = {"symbols": ["BEL"], "company_facts": {}, "evidence": [], "data_freshness": {}}

        with _patch_repos(company, [fin_row], [action_row]):
            out = await factual_company_context_node(state, session=AsyncMock())

        bel_facts = out["company_facts"]["BEL"]
        assert bel_facts["financials"][0]["pat"] == 131606.0
        assert bel_facts["financials"][0]["statement_scope"] == "STANDALONE"
        assert bel_facts["financials"][0]["verification_status"] == "unverified"
        assert bel_facts["corporate_actions"][0]["amount"] == 0.55
        assert bel_facts["corporate_actions"][0]["verification_status"] == "verified"

        assert len(out["evidence"]) == 2
        for item in out["evidence"]:
            assert "source" in item
            assert "is_confirmed" in item

        assert "fundamentals" in out["data_freshness"]

    @pytest.mark.asyncio
    async def test_unconfirmed_fact_marked_not_confirmed_in_evidence(self) -> None:
        company = MagicMock(id=uuid.uuid4(), symbol="BEL")
        fin_row = _financial_row()
        assert fin_row.verification_status == "unverified"

        state = {"symbols": ["BEL"], "company_facts": {}, "evidence": [], "data_freshness": {}}

        with _patch_repos(company, [fin_row], []):
            out = await factual_company_context_node(state, session=AsyncMock())

        financial_evidence = [e for e in out["evidence"] if "revenue" in e["excerpt"]]
        assert financial_evidence[0]["is_confirmed"] is False

    @pytest.mark.asyncio
    async def test_unknown_symbol_skipped_without_error(self) -> None:
        company = None
        state = {"symbols": ["NOPE"], "company_facts": {}, "evidence": [], "data_freshness": {}}

        with _patch_repos(company) as stack:
            out = await factual_company_context_node(state, session=AsyncMock())
            _ = stack  # repos never called since company lookup returns None

        assert out["company_facts"] == {}
        assert out["evidence"] == []

    @pytest.mark.asyncio
    async def test_no_symbols_returns_empty_facts(self) -> None:
        state = {"symbols": [], "company_facts": {}, "evidence": [], "data_freshness": {}}
        out = await factual_company_context_node(state, session=AsyncMock())
        assert out["company_facts"] == {}
        assert out["evidence"] == []
        assert out["data_freshness"] == {}

    @pytest.mark.asyncio
    async def test_does_not_import_nse_source(self) -> None:
        """The node must read exclusively from PostgreSQL — never NSE/BSE
        directly during a normal agent query."""
        import investing_agent.agents.nodes.factual_company_context as mod

        assert not hasattr(mod, "NSEDataSource")
        assert not hasattr(mod, "BSEDataSource")


class TestPhase3BFacts:
    @pytest.mark.asyncio
    async def test_order_book_served_with_unverified_default(self) -> None:
        company = MagicMock(id=uuid.uuid4(), symbol="BEL")
        row = _order_book_row()
        state = {"symbols": ["BEL"], "company_facts": {}, "evidence": [], "data_freshness": {}}

        with _patch_repos(company, order_book_rows=[row]):
            out = await factual_company_context_node(state, session=AsyncMock())

        order_book = out["company_facts"]["BEL"]["order_book"]
        assert order_book[0]["order_book_value"] == 75000.0
        assert order_book[0]["verification_status"] == "UNVERIFIED"
        assert order_book[0]["source_document_id"] == str(row.source_document_id)

        ob_evidence = [e for e in out["evidence"] if "75,000 crore" in e["excerpt"]]
        assert len(ob_evidence) == 1
        assert ob_evidence[0]["is_confirmed"] is False

    @pytest.mark.asyncio
    async def test_human_verified_order_book_is_confirmed_in_evidence(self) -> None:
        company = MagicMock(id=uuid.uuid4(), symbol="BEL")
        row = _order_book_row(verification_status="HUMAN_VERIFIED")
        state = {"symbols": ["BEL"], "company_facts": {}, "evidence": [], "data_freshness": {}}

        with _patch_repos(company, order_book_rows=[row]):
            out = await factual_company_context_node(state, session=AsyncMock())

        ob_evidence = [e for e in out["evidence"] if "75,000 crore" in e["excerpt"]]
        assert ob_evidence[0]["is_confirmed"] is True

    @pytest.mark.asyncio
    async def test_all_phase3b_fact_keys_present_even_when_empty(self) -> None:
        company = MagicMock(id=uuid.uuid4(), symbol="BEL")
        state = {"symbols": ["BEL"], "company_facts": {}, "evidence": [], "data_freshness": {}}

        with _patch_repos(company):
            out = await factual_company_context_node(state, session=AsyncMock())

        bel_facts = out["company_facts"]["BEL"]
        for key in (
            "order_book", "guidance", "segment_metrics",
            "operational_metrics", "capacity_updates", "commentary",
        ):
            assert bel_facts[key] == []
