from __future__ import annotations

"""Integration tests for Phase 6B portfolio reconstruction against a real
PostgreSQL database. Seeds HistoricalTrade/HistoricalCashFlow rows directly
via repositories (not via file import -- that's already covered by
test_broker_history_integration.py) to keep this focused on the
replay/FIFO/reconciliation logic itself.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d
"""

import uuid
from datetime import date
from decimal import Decimal

import openpyxl
import pytest

from investing_agent.schemas.broker_history import (
    BrokerAccountCreate,
    HistoricalCashFlowCreate,
    HistoricalTradeCreate,
)
from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.corporate_actions import CorporateActionCreate
from investing_agent.services.reconstruction.pnl_report_parser import (
    HOLDINGS_EQUITY_HEADER,
    PNL_EQUITY_HEADER,
)
from investing_agent.services.reconstruction.reconciliation import reconcile_account
from investing_agent.services.reconstruction.service import (
    get_portfolio_as_of,
    reconstruct_and_persist,
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
async def broker_account(db_session):
    from investing_agent.db.repositories.broker_history import BrokerAccountRepository

    repo = BrokerAccountRepository(db_session)
    account, _ = await repo.get_or_create(
        BrokerAccountCreate(
            user_id=f"user-{uuid.uuid4().hex[:8]}", broker="ZERODHA", account_label="primary"
        )
    )
    await db_session.flush()
    return account


async def _seed_trade(db_session, broker_account, symbol, side, quantity, price, trade_date, key):
    from investing_agent.db.repositories.broker_history import HistoricalTradeRepository

    repo = HistoricalTradeRepository(db_session)
    await repo.create_if_not_exists(
        HistoricalTradeCreate(
            broker_account_id=broker_account.id,
            dedupe_key=key,
            symbol=symbol,
            trade_date=trade_date,
            side=side,
            quantity=Decimal(str(quantity)),
            price=Decimal(str(price)),
        )
    )
    await db_session.flush()


async def _seed_cash_flow(db_session, broker_account, flow_date, amount, flow_type, key):
    from investing_agent.db.repositories.broker_history import HistoricalCashFlowRepository

    repo = HistoricalCashFlowRepository(db_session)
    await repo.create_if_not_exists(
        HistoricalCashFlowCreate(
            broker_account_id=broker_account.id,
            dedupe_key=key,
            flow_date=flow_date,
            flow_type=flow_type,
            amount=Decimal(str(amount)),
        )
    )
    await db_session.flush()


class TestGetPortfolioAsOf:
    async def test_reconstructs_open_position_and_realized_pnl(self, db_session, broker_account) -> None:
        await _seed_trade(db_session, broker_account, "INFY", "BUY", 10, 1000, date(2024, 1, 1), "T1")
        await _seed_trade(db_session, broker_account, "INFY", "SELL", 4, 1200, date(2024, 2, 1), "T2")
        await _seed_cash_flow(db_session, broker_account, date(2024, 1, 1), 50000, "DEPOSIT", "C1")

        snapshot = await get_portfolio_as_of(
            db_session, broker_account_id=broker_account.id, as_of_date=date(2024, 3, 1)
        )

        assert snapshot.strategy_profile == "LONG_TERM"
        position = next(p for p in snapshot.positions if p.symbol == "INFY")
        assert position.quantity_held == Decimal("6")
        assert position.average_cost == Decimal("1000")
        assert position.realized_pnl_cumulative == Decimal("800")  # 4 * 200
        assert snapshot.realized_pnl_cumulative_total == Decimal("800")
        assert not snapshot.warnings

    async def test_cash_balance_always_carries_caveat(self, db_session, broker_account) -> None:
        await _seed_trade(db_session, broker_account, "INFY", "BUY", 1, 100, date(2024, 1, 1), "T1")

        snapshot = await get_portfolio_as_of(
            db_session, broker_account_id=broker_account.id, as_of_date=date(2024, 3, 1)
        )
        assert snapshot.cash_balance_caveat  # never empty
        assert snapshot.cash_balance_partial == Decimal("-100")

    async def test_oversell_surfaces_warning_not_crash(self, db_session, broker_account) -> None:
        await _seed_trade(db_session, broker_account, "XYZ", "BUY", 5, 100, date(2024, 1, 1), "T1")
        await _seed_trade(db_session, broker_account, "XYZ", "SELL", 20, 50, date(2024, 2, 1), "T2")

        snapshot = await get_portfolio_as_of(
            db_session, broker_account_id=broker_account.id, as_of_date=date(2024, 3, 1)
        )
        assert len(snapshot.warnings) == 1
        assert "SYNTHETIC_LOT_CORPORATE_ACTION_SUSPECTED" in snapshot.warnings[0]
        position = next(p for p in snapshot.positions if p.symbol == "XYZ") if any(
            p.symbol == "XYZ" for p in snapshot.positions
        ) else None
        assert position is None  # fully closed out (real + synthetic), no open lots


class TestReconstructAndPersist:
    async def test_rebuilds_position_lots_wholesale(self, db_session, broker_account) -> None:
        from investing_agent.db.repositories.broker_history import HistoricalPositionLotRepository

        await _seed_trade(db_session, broker_account, "INFY", "BUY", 10, 1000, date(2024, 1, 1), "T1")

        result = await reconstruct_and_persist(db_session, broker_account_id=broker_account.id)
        assert result.lots_written == 1
        assert result.symbols_with_open_positions == 1

        lot_repo = HistoricalPositionLotRepository(db_session)
        lots = await lot_repo.list_for_account(broker_account.id)
        assert len(lots) == 1
        assert lots[0].symbol == "INFY"
        assert lots[0].quantity_remaining == Decimal("10")

        # rerunning wholesale-replaces rather than duplicating
        await _seed_trade(db_session, broker_account, "TCS", "BUY", 5, 3000, date(2024, 1, 2), "T2")
        await reconstruct_and_persist(db_session, broker_account_id=broker_account.id)
        lots_after = await lot_repo.list_for_account(broker_account.id)
        assert len(lots_after) == 2


def _write_pnl_workbook(path, rows, period):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Equity"
    ws.append((None, "Client ID", "TEST"))
    ws.append((None, f"P&L Statement for Equity from {period}"))
    ws.append((None, "Summary"))
    ws.append((None, "Realized P&L", 0.0))
    ws.append((None, "Unrealized P&L", 0.0))
    ws.append((None, *PNL_EQUITY_HEADER))
    for row in rows:
        ws.append((None, *row))
    wb.save(path)


def _write_holdings_workbook(path, rows, as_of):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Equity"
    ws.append((None, "Client ID", "TEST"))
    ws.append((None, f"Equity Holdings Statement as on {as_of}"))
    ws.append((None, "Summary"))
    ws.append((None, "Invested Value", 0.0))
    ws.append((None, "Present Value", 0.0))
    ws.append((None, "Unrealized P&L", 0.0))
    ws.append((None, *HOLDINGS_EQUITY_HEADER))
    for row in rows:
        ws.append((None, *row))
    wb.save(path)


class TestReconcileAccount:
    async def test_matching_statement_produces_clean_report(self, db_session, broker_account, tmp_path) -> None:
        await _seed_trade(db_session, broker_account, "INFY", "BUY", 10, 1000, date(2024, 1, 1), "T1")
        await _seed_trade(db_session, broker_account, "INFY", "SELL", 4, 1200, date(2024, 2, 1), "T2")

        pnl_path = tmp_path / "pnl-TEST.xlsx"
        _write_pnl_workbook(
            pnl_path,
            [("INFY", "INE009A01021", 4.0, 4000.0, 4800.0, 800.0, 20.0, 1200.0, 6.0, "", 6000.0, 0.0, 0.0)],
            period="2024-01-01 to 2024-02-01",
        )

        report = await reconcile_account(
            db_session, broker_account_id=broker_account.id, statement_files=[pnl_path]
        )
        assert report.overall_status == "CLEAN"
        assert not report.row_diffs

    async def test_untracked_symbol_with_nonzero_open_qty_is_expected_gap(
        self, db_session, broker_account, tmp_path
    ) -> None:
        # no trades seeded for GHOST at all
        pnl_path = tmp_path / "pnl-TEST.xlsx"
        _write_pnl_workbook(
            pnl_path,
            [("GHOST", "INE000000001", 0.0, 0.0, 0.0, 0.0, 0.0, 100.0, 5.0, "", 500.0, 50.0, 10.0)],
            period="2024-01-01 to 2024-02-01",
        )

        report = await reconcile_account(
            db_session, broker_account_id=broker_account.id, statement_files=[pnl_path]
        )
        assert "GHOST" in report.expected_gaps_corporate_action
        assert "GHOST" not in report.divergent_symbols

    async def test_holdings_quantity_mismatch_is_divergent(self, db_session, broker_account, tmp_path) -> None:
        await _seed_trade(db_session, broker_account, "INFY", "BUY", 10, 1000, date(2024, 1, 1), "T1")

        holdings_path = tmp_path / "holdings-TEST.xlsx"
        _write_holdings_workbook(
            holdings_path,
            [("INFY", "INE009A01021", "IT", 100.0, 0.0, 100.0, 0.0, 0.0, 100.0, 1200.0, 0.0, 0.0)],
            as_of="2024-03-01",
        )

        report = await reconcile_account(
            db_session, broker_account_id=broker_account.id, statement_files=[holdings_path]
        )
        assert report.overall_status == "DIVERGENT"
        assert "INFY" in report.divergent_symbols
        assert any(d.field == "quantity_held" for d in report.row_diffs)


async def _seed_split(db_session, symbol, event_date, ratio, event_type="split"):
    from investing_agent.db.repositories.company import CompanyRepository
    from investing_agent.db.repositories.corporate_action import CorporateActionRepository

    company = await CompanyRepository(db_session).upsert(
        CompanyCreate(symbol=symbol, name=symbol, exchange="NSE")
    )
    await CorporateActionRepository(db_session).upsert_versioned(
        CorporateActionCreate(
            company_id=company.id,
            symbol=symbol,
            action_type=event_type,
            event_date=event_date,
            ratio=ratio,
            source_type="test_fixture",
        )
    )
    await db_session.flush()


async def _seed_opening_adjustment(
    db_session, broker_account, symbol, opening_date, quantity, cost_price
):
    from investing_agent.db.repositories.broker_history import (
        OpeningPositionAdjustmentRepository,
    )
    from investing_agent.schemas.corporate_actions import OpeningPositionAdjustmentCreate

    await OpeningPositionAdjustmentRepository(db_session).upsert(
        OpeningPositionAdjustmentCreate(
            broker_account_id=broker_account.id,
            symbol=symbol,
            opening_date=opening_date,
            quantity=Decimal(str(quantity)),
            cost_price=Decimal(str(cost_price)),
            source="ZERODHA_PNL_RECONCILIATION",
            confidence="MEDIUM",
            reason="MISSING_TRADE_HISTORY",
        )
    )
    await db_session.flush()


class TestCorporateActionAdjustmentInReconstruction:
    async def test_bonus_applied_to_open_position(self, db_session, broker_account) -> None:
        await _seed_trade(db_session, broker_account, "CDSL", "BUY", 20, 1102.10, date(2022, 5, 16), "T1")
        await _seed_split(db_session, "CDSL", date(2024, 8, 24), "1:2", event_type="bonus")

        snapshot = await get_portfolio_as_of(
            db_session, broker_account_id=broker_account.id, as_of_date=date(2026, 8, 19)
        )
        position = next(p for p in snapshot.positions if p.symbol == "CDSL")
        assert position.quantity_held == Decimal("40")
        assert position.average_cost == Decimal("551.05")
        assert len(snapshot.corporate_action_adjustments) == 1
        assert snapshot.corporate_action_adjustments[0].event_type == "BONUS"

    async def test_split_before_position_opened_is_ignored(self, db_session, broker_account) -> None:
        await _seed_split(db_session, "CDSL", date(2020, 1, 1), "1:2", event_type="split")
        await _seed_trade(db_session, broker_account, "CDSL", "BUY", 10, 100, date(2024, 1, 1), "T1")

        snapshot = await get_portfolio_as_of(
            db_session, broker_account_id=broker_account.id, as_of_date=date(2026, 8, 19)
        )
        position = next(p for p in snapshot.positions if p.symbol == "CDSL")
        assert position.quantity_held == Decimal("10")
        assert not snapshot.corporate_action_adjustments


class TestOpeningPositionAdjustmentInReconstruction:
    async def test_sell_with_no_prior_buy_resolved(self, db_session, broker_account) -> None:
        await _seed_trade(db_session, broker_account, "ZAGGLE", "SELL", 40, 201.80, date(2026, 8, 13), "T1")
        await _seed_opening_adjustment(db_session, broker_account, "ZAGGLE", date(2026, 8, 13), 40, 458.00)

        snapshot = await get_portfolio_as_of(
            db_session, broker_account_id=broker_account.id, as_of_date=date(2026, 8, 19)
        )
        assert all("SYNTHETIC_LOT_CORPORATE_ACTION_SUSPECTED" not in w for w in snapshot.warnings)
        assert any("OPENING_POSITION_ADJUSTMENT_APPLIED" in w for w in snapshot.warnings)
        assert snapshot.realized_pnl_cumulative_total == Decimal("-10248.00")
        assert not any(p.symbol == "ZAGGLE" for p in snapshot.positions)


class TestReconciliationQualityScore:
    async def test_clean_reconciliation_scores_100_percent(self, db_session, broker_account, tmp_path) -> None:
        await _seed_trade(db_session, broker_account, "INFY", "BUY", 10, 1000, date(2024, 1, 1), "T1")
        await _seed_trade(db_session, broker_account, "INFY", "SELL", 4, 1200, date(2024, 2, 1), "T2")

        pnl_path = tmp_path / "pnl-TEST.xlsx"
        _write_pnl_workbook(
            pnl_path,
            [("INFY", "INE009A01021", 4.0, 4000.0, 4800.0, 800.0, 20.0, 1200.0, 6.0, "", 6000.0, 0.0, 0.0)],
            period="2024-01-01 to 2024-02-01",
        )
        holdings_path = tmp_path / "holdings-TEST.xlsx"
        _write_holdings_workbook(
            holdings_path,
            [("INFY", "INE009A01021", "IT", 6.0, 0.0, 6.0, 0.0, 0.0, 1000.0, 1200.0, 0.0, 0.0)],
            as_of="2024-03-01",
        )

        report = await reconcile_account(
            db_session, broker_account_id=broker_account.id, statement_files=[pnl_path, holdings_path]
        )
        assert report.overall_status == "CLEAN"
        assert report.quality_score.holdings_quantity_match_pct == 100.0
        assert report.quality_score.holdings_avg_cost_match_pct == 100.0
        assert report.quality_score.realized_pnl_match_pct == 100.0
        assert report.quality_score.unresolved_symbol_count == 0

    async def test_divergent_holdings_reduces_quality_score(
        self, db_session, broker_account, tmp_path
    ) -> None:
        await _seed_trade(db_session, broker_account, "INFY", "BUY", 10, 1000, date(2024, 1, 1), "T1")

        holdings_path = tmp_path / "holdings-TEST.xlsx"
        _write_holdings_workbook(
            holdings_path,
            [("INFY", "INE009A01021", "IT", 100.0, 0.0, 100.0, 0.0, 0.0, 100.0, 1200.0, 0.0, 0.0)],
            as_of="2024-03-01",
        )

        report = await reconcile_account(
            db_session, broker_account_id=broker_account.id, statement_files=[holdings_path]
        )
        assert report.quality_score.holdings_quantity_match_pct == 0.0
        assert report.quality_score.unresolved_symbol_count == 1
        assert report.quality_score.realized_pnl_match_pct is None  # no pnl file checked
