from __future__ import annotations

"""Generic import orchestration (Phase 6A) -- shared by every broker.

Owns exactly the parts of the pipeline that are provider-neutral:
run-level idempotency (file_hash), row-level idempotency (dedupe_key),
symbol -> company_id resolution, and HistoricalImportRun bookkeeping. The
per-broker StatementImporter only ever needs to implement parse(); it never
touches the database.
"""

import hashlib
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.config.logging import get_logger
from investing_agent.db.models import HistoricalImportRun
from investing_agent.db.repositories.broker_history import (
    HistoricalCashFlowRepository,
    HistoricalDividendRepository,
    HistoricalImportRunRepository,
    HistoricalTradeRepository,
)
from investing_agent.db.repositories.company import CompanyRepository
from investing_agent.schemas.broker_history import (
    HistoricalCashFlowCreate,
    HistoricalDividendCreate,
    HistoricalTradeCreate,
)
from investing_agent.services.ingestion.broker_history.base import StatementImporter

logger = get_logger(__name__)


async def import_statement_file(
    session: AsyncSession,
    *,
    broker_account_id: uuid.UUID,
    importer: StatementImporter,
    file_path: Path,
) -> HistoricalImportRun:
    """Idempotently import one statement file for a broker account.

    Run-level idempotency: a byte-identical file (same file_hash) for the
    same account returns the existing run untouched, without re-parsing.
    Row-level idempotency: trades/cash flows/dividends are additionally
    deduplicated on (broker_account_id, dedupe_key), so re-importing a file
    whose date range overlaps a previous import (Zerodha Console tradebook
    exports are capped at 365 days) still merges safely rather than
    duplicating.
    """
    file_bytes = file_path.read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    run_repo = HistoricalImportRunRepository(session)
    existing_run = await run_repo.get_by_file_hash(broker_account_id, file_hash)
    if existing_run is not None:
        logger.info(
            "broker_import_run_reused",
            broker_account_id=str(broker_account_id),
            file_name=file_path.name,
            run_id=str(existing_run.id),
        )
        return existing_run

    run = HistoricalImportRun(
        broker_account_id=broker_account_id,
        source_type=f"{importer.broker.lower()}_unknown",
        file_name=file_path.name,
        file_hash=file_hash,
        status="PARTIAL",
    )
    await run_repo.add(run)

    try:
        parsed = importer.parse(file_path)
    except Exception as exc:
        run.status = "FAILED"
        run.error_message = str(exc)
        await session.flush()
        logger.error(
            "broker_import_parse_failed",
            broker_account_id=str(broker_account_id),
            file_name=file_path.name,
            error=str(exc),
        )
        raise

    company_repo = CompanyRepository(session)
    trade_repo = HistoricalTradeRepository(session)
    cash_repo = HistoricalCashFlowRepository(session)
    dividend_repo = HistoricalDividendRepository(session)

    company_cache: dict[str, uuid.UUID | None] = {}

    async def _resolve_company_id(symbol: str) -> uuid.UUID | None:
        if symbol not in company_cache:
            company = await company_repo.get_by_symbol(symbol)
            company_cache[symbol] = company.id if company else None
        return company_cache[symbol]

    trades_created = trades_skipped = 0
    for t in parsed.trades:
        company_id = await _resolve_company_id(t.symbol)
        _, created = await trade_repo.create_if_not_exists(
            HistoricalTradeCreate(
                broker_account_id=broker_account_id,
                dedupe_key=t.dedupe_key,
                symbol=t.symbol,
                isin=t.isin,
                company_id=company_id,
                trade_date=t.trade_date,
                trade_datetime=t.trade_datetime,
                side=t.side,
                quantity=t.quantity,
                price=t.price,
                exchange=t.exchange,
                segment=t.segment,
                product=t.product,
                order_id=t.order_id,
                charges=t.charges,
                source_import_run_id=run.id,
                raw=t.raw,
            )
        )
        trades_created += created
        trades_skipped += not created

    cash_flows_created = cash_flows_skipped = 0
    for cf in parsed.cash_flows:
        _, created = await cash_repo.create_if_not_exists(
            HistoricalCashFlowCreate(
                broker_account_id=broker_account_id,
                dedupe_key=cf.dedupe_key,
                flow_date=cf.flow_date,
                flow_type=cf.flow_type,
                amount=cf.amount,
                description=cf.description,
                source_import_run_id=run.id,
                raw=cf.raw,
            )
        )
        cash_flows_created += created
        cash_flows_skipped += not created

    dividends_created = dividends_skipped = 0
    for d in parsed.dividends:
        company_id = await _resolve_company_id(d.symbol)
        _, created = await dividend_repo.create_if_not_exists(
            HistoricalDividendCreate(
                broker_account_id=broker_account_id,
                dedupe_key=d.dedupe_key,
                symbol=d.symbol,
                isin=d.isin,
                company_id=company_id,
                payment_date=d.payment_date,
                quantity_held=d.quantity_held,
                amount=d.amount,
                source_import_run_id=run.id,
                raw=d.raw,
            )
        )
        dividends_created += created
        dividends_skipped += not created

    run.source_type = parsed.source_type
    run.status = "SUCCESS"
    run.trades_created = trades_created
    run.trades_skipped_duplicate = trades_skipped
    run.cash_flows_created = cash_flows_created
    run.cash_flows_skipped_duplicate = cash_flows_skipped
    run.dividends_created = dividends_created
    run.dividends_skipped_duplicate = dividends_skipped
    run.date_range_start = parsed.date_range_start
    run.date_range_end = parsed.date_range_end
    run.warnings = parsed.warnings or None
    await session.flush()

    logger.info(
        "broker_import_run_completed",
        broker_account_id=str(broker_account_id),
        file_name=file_path.name,
        run_id=str(run.id),
        trades_created=trades_created,
        trades_skipped_duplicate=trades_skipped,
        cash_flows_created=cash_flows_created,
        cash_flows_skipped_duplicate=cash_flows_skipped,
        dividends_created=dividends_created,
        dividends_skipped_duplicate=dividends_skipped,
    )
    return run
