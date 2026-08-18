from __future__ import annotations

"""Unit tests for the factual_company_context LangGraph node.

Core rule under test: this node reads only from PostgreSQL (via repositories)
and never talks to NSE/BSE directly. Every fact it returns must carry
verification_status and source_document_id so downstream nodes/UI can tell a
cross-checked FACT apart from an unverified JSON hint.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from investing_agent.agents.nodes.factual_company_context import (
    factual_company_context_node,
)


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


def _patched_repos(company, financial_rows, action_rows):
    company_repo = AsyncMock()
    company_repo.get_by_symbol = AsyncMock(return_value=company)
    financial_repo = AsyncMock()
    financial_repo.list_by_company = AsyncMock(return_value=financial_rows)
    action_repo = AsyncMock()
    action_repo.list_by_company = AsyncMock(return_value=action_rows)

    return (
        patch(
            "investing_agent.agents.nodes.factual_company_context.CompanyRepository",
            return_value=company_repo,
        ),
        patch(
            "investing_agent.agents.nodes.factual_company_context.FinancialResultRepository",
            return_value=financial_repo,
        ),
        patch(
            "investing_agent.agents.nodes.factual_company_context.CorporateActionRepository",
            return_value=action_repo,
        ),
    )


class TestFactualCompanyContextNode:
    @pytest.mark.asyncio
    async def test_serves_facts_and_evidence_from_db(self) -> None:
        company = MagicMock(id=uuid.uuid4(), symbol="BEL")
        fin_row, action_row = _financial_row(), _action_row()

        p1, p2, p3 = _patched_repos(company, [fin_row], [action_row])
        state = {"symbols": ["BEL"], "company_facts": {}, "evidence": [], "data_freshness": {}}

        with p1, p2, p3:
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

        p1, p2, p3 = _patched_repos(company, [fin_row], [])
        state = {"symbols": ["BEL"], "company_facts": {}, "evidence": [], "data_freshness": {}}

        with p1, p2, p3:
            out = await factual_company_context_node(state, session=AsyncMock())

        financial_evidence = [e for e in out["evidence"] if "revenue" in e["excerpt"]]
        assert financial_evidence[0]["is_confirmed"] is False

    @pytest.mark.asyncio
    async def test_unknown_symbol_skipped_without_error(self) -> None:
        company_repo = AsyncMock()
        company_repo.get_by_symbol = AsyncMock(return_value=None)
        financial_repo = AsyncMock()
        action_repo = AsyncMock()

        state = {"symbols": ["NOPE"], "company_facts": {}, "evidence": [], "data_freshness": {}}

        with (
            patch(
                "investing_agent.agents.nodes.factual_company_context.CompanyRepository",
                return_value=company_repo,
            ),
            patch(
                "investing_agent.agents.nodes.factual_company_context.FinancialResultRepository",
                return_value=financial_repo,
            ),
            patch(
                "investing_agent.agents.nodes.factual_company_context.CorporateActionRepository",
                return_value=action_repo,
            ),
        ):
            out = await factual_company_context_node(state, session=AsyncMock())

        assert out["company_facts"] == {}
        assert out["evidence"] == []
        financial_repo.list_by_company.assert_not_called()
        action_repo.list_by_company.assert_not_called()

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
