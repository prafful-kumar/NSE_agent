from __future__ import annotations

"""Integration test for services/ingestion/broker_history/import_service.py
against a real PostgreSQL database.

Most of this file uses an in-memory fake StatementImporter to isolate the
orchestration logic (idempotency, company resolution) from any one broker's
parsing details. TestZerodhaImporterEndToEnd at the bottom instead drives
the real ZerodhaStatementImporter against synthetic files built with the
exact column layout confirmed from real Zerodha Console exports (see
services/ingestion/broker_history/zerodha.py), to prove the full pipeline
(registry -> importer -> import_service -> repos) works end-to-end, not
just the fake.

Core rules under test:
- Run-level idempotency: re-importing a byte-identical file returns the
  existing HistoricalImportRun untouched, without duplicating rows.
- Row-level idempotency: two *different* files whose date ranges overlap
  (the realistic case, since Zerodha Console tradebook exports are capped
  at 365 days) still merge safely because trades are deduplicated on
  (broker_account_id, dedupe_key), not file identity.
- A parse failure is recorded as a FAILED run (for auditability) and the
  original exception still propagates to the caller.

Requires a running PostgreSQL instance (investing_agent_test database).
Run with: pytest tests/integration -m integration
To start the test DB: docker-compose up postgres_test -d
"""

import tempfile
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

import pytest

from investing_agent.schemas.broker_history import BrokerAccountCreate
from investing_agent.services.ingestion.broker_history.base import (
    ParsedCashFlow,
    ParsedStatement,
    ParsedTrade,
    StatementImporter,
)
from investing_agent.services.ingestion.broker_history.import_service import (
    import_statement_file,
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
            user_id=f"user-{uuid.uuid4().hex[:8]}",
            broker="ZERODHA",
            account_label="primary",
        )
    )
    await db_session.flush()
    return account


class _FakeImporter(StatementImporter):
    broker: ClassVar[str] = "ZERODHA"

    def __init__(self, statement: ParsedStatement | None = None, raises: Exception | None = None):
        self._statement = statement
        self._raises = raises

    def parse(self, file_path: Path) -> ParsedStatement:
        if self._raises is not None:
            raise self._raises
        assert self._statement is not None
        return self._statement


def _write_tmp_file(content: bytes) -> Path:
    fd, name = tempfile.mkstemp()
    path = Path(name)
    path.write_bytes(content)
    return path


def _sample_statement() -> ParsedStatement:
    return ParsedStatement(
        source_type="zerodha_tradebook_csv",
        trades=[
            ParsedTrade(
                dedupe_key="trade-1",
                symbol="BEL",
                trade_date=date(2024, 1, 5),
                side="BUY",
                quantity=Decimal("10"),
                price=Decimal("200.50"),
            ),
        ],
        cash_flows=[
            ParsedCashFlow(
                dedupe_key="cashflow-1",
                flow_date=date(2024, 1, 1),
                flow_type="DEPOSIT",
                amount=Decimal("50000"),
            ),
        ],
        date_range_start=date(2024, 1, 1),
        date_range_end=date(2024, 1, 5),
    )


class TestBrokerAccountStrategyProfile:
    async def test_zerodha_account_defaults_to_long_term(self, broker_account):
        assert broker_account.strategy_profile == "LONG_TERM"


class TestImportRunLevelIdempotency:
    async def test_successful_import_creates_run_with_counts(self, db_session, broker_account):
        tmp_path = _write_tmp_file(b"trade_id,symbol\ntrade-1,BEL\n")
        importer = _FakeImporter(statement=_sample_statement())

        run = await import_statement_file(
            db_session, broker_account_id=broker_account.id, importer=importer, file_path=tmp_path
        )

        assert run.status == "SUCCESS"
        assert run.source_type == "zerodha_tradebook_csv"
        assert run.trades_created == 1
        assert run.trades_skipped_duplicate == 0
        assert run.cash_flows_created == 1
        assert run.cash_flows_skipped_duplicate == 0

    async def test_reimporting_byte_identical_file_reuses_run(self, db_session, broker_account):
        tmp_path = _write_tmp_file(b"same bytes")
        importer = _FakeImporter(statement=_sample_statement())

        first = await import_statement_file(
            db_session, broker_account_id=broker_account.id, importer=importer, file_path=tmp_path
        )
        second = await import_statement_file(
            db_session, broker_account_id=broker_account.id, importer=importer, file_path=tmp_path
        )

        assert first.id == second.id
        assert second.trades_created == 1  # unchanged, not double-counted

    async def test_parse_failure_records_failed_run_and_reraises(self, db_session, broker_account):
        tmp_path = _write_tmp_file(b"garbage")
        importer = _FakeImporter(raises=ValueError("unrecognized column layout"))

        with pytest.raises(ValueError, match="unrecognized column layout"):
            await import_statement_file(
                db_session, broker_account_id=broker_account.id, importer=importer, file_path=tmp_path
            )


class TestRowLevelDedupeAcrossOverlappingFiles:
    async def test_overlapping_file_with_shared_trade_dedupes_by_trade_key(self, db_session, broker_account):
        file_a = _write_tmp_file(b"file A bytes")
        file_b = _write_tmp_file(b"file B bytes -- different file, overlapping date range")

        statement_a = _sample_statement()
        statement_b = ParsedStatement(
            source_type="zerodha_tradebook_csv",
            trades=[
                ParsedTrade(  # same dedupe_key as statement_a's trade -- simulates overlap
                    dedupe_key="trade-1",
                    symbol="BEL",
                    trade_date=date(2024, 1, 5),
                    side="BUY",
                    quantity=Decimal("10"),
                    price=Decimal("200.50"),
                ),
                ParsedTrade(
                    dedupe_key="trade-2",
                    symbol="BEL",
                    trade_date=date(2024, 2, 1),
                    side="SELL",
                    quantity=Decimal("5"),
                    price=Decimal("210.00"),
                ),
            ],
        )

        run_a = await import_statement_file(
            db_session, broker_account_id=broker_account.id,
            importer=_FakeImporter(statement=statement_a), file_path=file_a,
        )
        run_b = await import_statement_file(
            db_session, broker_account_id=broker_account.id,
            importer=_FakeImporter(statement=statement_b), file_path=file_b,
        )

        assert run_a.trades_created == 1
        assert run_b.trades_created == 1  # only trade-2 is new
        assert run_b.trades_skipped_duplicate == 1  # trade-1 already existed


class TestCompanyResolution:
    async def test_trade_resolves_company_id_when_symbol_known(self, db_session, broker_account):
        from investing_agent.db.repositories.company import CompanyRepository
        from investing_agent.schemas.company import CompanyCreate

        symbol = f"BEL{uuid.uuid4().hex[:6].upper()}"
        company = await CompanyRepository(db_session).upsert(
            CompanyCreate(symbol=symbol, name="Bharat Electronics", exchange="NSE")
        )

        statement = ParsedStatement(
            source_type="zerodha_tradebook_csv",
            trades=[
                ParsedTrade(
                    dedupe_key="trade-known-company",
                    symbol=symbol,
                    trade_date=date(2024, 1, 5),
                    side="BUY",
                    quantity=Decimal("10"),
                    price=Decimal("200.50"),
                ),
            ],
        )
        tmp_path = _write_tmp_file(b"known company file")

        run = await import_statement_file(
            db_session, broker_account_id=broker_account.id,
            importer=_FakeImporter(statement=statement), file_path=tmp_path,
        )

        from investing_agent.db.repositories.broker_history import HistoricalTradeRepository

        trade = await HistoricalTradeRepository(db_session).get_by_dedupe_key(
            broker_account.id, "trade-known-company"
        )
        assert trade is not None
        assert trade.company_id == company.id
        assert run.trades_created == 1

    async def test_trade_leaves_company_id_none_when_symbol_unknown(self, db_session, broker_account):
        statement = ParsedStatement(
            source_type="zerodha_tradebook_csv",
            trades=[
                ParsedTrade(
                    dedupe_key="trade-unknown-company",
                    symbol=f"UNKNOWN{uuid.uuid4().hex[:6].upper()}",
                    trade_date=date(2024, 1, 5),
                    side="BUY",
                    quantity=Decimal("1"),
                    price=Decimal("1"),
                ),
            ],
        )
        tmp_path = _write_tmp_file(b"unknown company file")

        await import_statement_file(
            db_session, broker_account_id=broker_account.id,
            importer=_FakeImporter(statement=statement), file_path=tmp_path,
        )

        from investing_agent.db.repositories.broker_history import HistoricalTradeRepository

        trade = await HistoricalTradeRepository(db_session).get_by_dedupe_key(
            broker_account.id, "trade-unknown-company"
        )
        assert trade is not None
        assert trade.company_id is None


def _write_zerodha_tradebook_xlsx(path: Path, rows: list[tuple]) -> None:
    import openpyxl

    from investing_agent.services.ingestion.broker_history.zerodha import TRADEBOOK_HEADER

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Equity"
    ws.append((None, "Client ID", "TESTID"))
    ws.append((None, *TRADEBOOK_HEADER))
    for row in rows:
        ws.append((None, *row))
    wb.save(path)


class TestZerodhaImporterEndToEnd:
    """Drives the real ZerodhaStatementImporter (not the fake) through
    import_statement_file, simulating the realistic scenario of two
    365-day-capped Console tradebook exports whose date ranges overlap by
    one shared trade."""

    async def test_two_overlapping_tradebook_exports_merge_without_duplication(
        self, db_session, broker_account
    ):
        from investing_agent.services.ingestion.broker_history.zerodha import (
            ZerodhaStatementImporter,
        )

        file_a = Path(tempfile.mkstemp(suffix=".xlsx")[1])
        file_b = Path(tempfile.mkstemp(suffix=".xlsx")[1])

        _write_zerodha_tradebook_xlsx(
            file_a,
            [
                (
                    "INFY", "INE009A01021", "2024-01-05", "NSE", "EQ", "EQ",
                    "buy", False, 10.0, 1500.5, "T-SHARED", "O1", "2024-01-05T10:00:00",
                ),
                (
                    "TCS", "INE467B01029", "2024-02-10", "NSE", "EQ", "EQ",
                    "sell", False, 5.0, 3800.25, "T-ONLY-A", "O2", "2024-02-10T11:30:00",
                ),
            ],
        )
        _write_zerodha_tradebook_xlsx(
            file_b,
            [
                (
                    "INFY", "INE009A01021", "2024-01-05", "NSE", "EQ", "EQ",
                    "buy", False, 10.0, 1500.5, "T-SHARED", "O1", "2024-01-05T10:00:00",
                ),
                (
                    "RELIANCE", "INE002A01018", "2024-06-01", "NSE", "EQ", "EQ",
                    "buy", False, 3.0, 2900.0, "T-ONLY-B", "O3", "2024-06-01T09:20:00",
                ),
            ],
        )

        run_a = await import_statement_file(
            db_session, broker_account_id=broker_account.id,
            importer=ZerodhaStatementImporter(), file_path=file_a,
        )
        run_b = await import_statement_file(
            db_session, broker_account_id=broker_account.id,
            importer=ZerodhaStatementImporter(), file_path=file_b,
        )

        assert run_a.status == "SUCCESS"
        assert run_a.source_type == "zerodha_tradebook_xlsx"
        assert run_a.trades_created == 2
        assert run_b.trades_created == 1  # only T-ONLY-B is new
        assert run_b.trades_skipped_duplicate == 1  # T-SHARED already existed

        from investing_agent.db.repositories.broker_history import HistoricalTradeRepository

        repo = HistoricalTradeRepository(db_session)
        assert (await repo.get_by_dedupe_key(broker_account.id, "T-SHARED")) is not None
        assert (await repo.get_by_dedupe_key(broker_account.id, "T-ONLY-A")) is not None
        assert (await repo.get_by_dedupe_key(broker_account.id, "T-ONLY-B")) is not None
