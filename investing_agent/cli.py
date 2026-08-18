from __future__ import annotations

"""CLI entry point.

Usage:
    python -m investing_agent.cli sync-portfolio
    python -m investing_agent.cli ping-broker
    python -m investing_agent.cli portfolio-status

Run 'python -m investing_agent.cli --help' for all commands.
"""

import asyncio
import sys
import uuid
from datetime import UTC, datetime

import click

from investing_agent.config.logging import configure_logging
from investing_agent.config.settings import get_settings


@click.group()
@click.option("--debug", is_flag=True, help="Enable debug logging")
def cli(debug: bool) -> None:
    settings = get_settings()
    configure_logging(
        log_level="DEBUG" if debug else settings.log_level,
        json_output=not settings.is_development,
    )


@cli.command("sync-portfolio")
@click.option("--user-id", default=None, help="User ID (defaults to DEFAULT_USER_ID from .env)")
@click.option("--force", is_flag=True, help="Skip staleness check and always sync")
def sync_portfolio(user_id: str | None, force: bool) -> None:
    """Sync portfolio from Zerodha Kite MCP and persist a snapshot.

    \b
    Pipeline:
        Zerodha MCP → normalize → validate → P&L → persist → report
    """
    asyncio.run(_sync_portfolio(user_id, force))


async def _sync_portfolio(user_id: str | None, force: bool) -> None:
    settings = get_settings()
    uid = user_id or settings.default_user_id

    click.echo(f"[{datetime.now().isoformat()}] Syncing portfolio for user: {uid}")

    # Determine which reader to use
    if settings.zerodha_access_token:
        from investing_agent.gateway.zerodha_reader import ZerodhaKiteMCPReader
        reader = ZerodhaKiteMCPReader(settings)
        click.echo("  Reader: ZerodhaKiteMCPReader (live)")
    else:
        from investing_agent.gateway.mock_reader import MockPortfolioReader
        reader = MockPortfolioReader(scenario="normal")
        click.echo("  Reader: MockPortfolioReader (no ZERODHA_ACCESS_TOKEN set)")

    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.portfolio_sync import PortfolioSyncService

    async with AsyncSessionLocal() as session:
        service = PortfolioSyncService(reader=reader, session=session)
        try:
            result = await service.sync(uid)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            click.echo(f"  ERROR: {exc}", err=True)
            sys.exit(1)

    # ── Reconciliation report ─────────────────────────────────────────────────
    click.echo(f"\n{'='*60}")
    click.echo("PORTFOLIO SYNC REPORT")
    click.echo(f"{'='*60}")
    click.echo(f"  Snapshot ID   : {result.snapshot_id}")
    click.echo(f"  Holdings      : {result.holdings_count}")
    click.echo(f"  Total value   : ₹{float(result.total_value):>12,.2f}")
    click.echo(f"  Invested      : ₹{float(result.total_invested):>12,.2f}")
    click.echo(f"  Unrealized P&L: ₹{float(result.total_pnl):>12,.2f}"
               f"  ({float(result.pnl_pct):+.2f}%)")
    click.echo(f"  Source        : {result.source}")
    click.echo(f"  Timestamp     : {result.sync_timestamp.isoformat()}")

    click.echo(f"\n{'─'*60}")
    click.echo("HOLDINGS")
    click.echo(f"{'─'*60}")
    click.echo(f"  {'Symbol':<12} {'Qty':>6} {'AvgPrice':>10} {'LTP':>10} "
               f"{'Value':>12} {'P&L':>10} {'Wt%':>6} {'Src'}")
    click.echo(f"  {'─'*12} {'─'*6} {'─'*10} {'─'*10} {'─'*12} {'─'*10} {'─'*6} {'─'*10}")

    for h in sorted(result.holdings, key=lambda x: float(x.portfolio_weight_pct), reverse=True):
        click.echo(
            f"  {h.tradingsymbol:<12} {h.quantity:>6} "
            f"₹{float(h.average_price):>9,.2f} "
            f"₹{float(h.last_price):>9,.2f} "
            f"₹{float(h.current_value):>11,.2f} "
            f"₹{float(h.pnl):>9,.2f} "
            f"{float(h.portfolio_weight_pct):>5.1f}% "
            f"{h.resolution_method[:8]}"
        )

    if result.warnings:
        click.echo(f"\n{'─'*60}")
        click.echo(f"WARNINGS ({len(result.warnings)})")
        for w in result.warnings:
            click.echo(f"  [{w.code}] {w.message}")

    if result.errors:
        click.echo(f"\n{'─'*60}")
        click.echo(f"ERRORS ({len(result.errors)})")
        for e in result.errors:
            click.echo(f"  {e}", err=True)

    click.echo(f"\n{'='*60}")
    status = "OK" if not result.has_errors else "PARTIAL"
    click.echo(f"Status: {status}")

    if result.has_errors:
        sys.exit(1)


@cli.command("ping-broker")
def ping_broker() -> None:
    """Check connectivity to the broker (Zerodha Kite MCP)."""
    asyncio.run(_ping_broker())


async def _ping_broker() -> None:
    settings = get_settings()
    if settings.zerodha_access_token:
        from investing_agent.gateway.zerodha_reader import ZerodhaKiteMCPReader
        reader = ZerodhaKiteMCPReader(settings)
        click.echo("Checking Kite MCP connectivity...")
    else:
        from investing_agent.gateway.mock_reader import MockPortfolioReader
        reader = MockPortfolioReader(scenario="normal")
        click.echo("No ZERODHA_ACCESS_TOKEN — using mock reader for ping.")

    reachable = await reader.ping()
    if reachable:
        click.echo("  ✓ Broker is reachable")
    else:
        click.echo("  ✗ Broker is NOT reachable", err=True)
        sys.exit(1)


@cli.command("portfolio-status")
@click.option("--user-id", default=None)
def portfolio_status(user_id: str | None) -> None:
    """Show the latest portfolio snapshot from the database."""
    asyncio.run(_portfolio_status(user_id))


async def _portfolio_status(user_id: str | None) -> None:
    settings = get_settings()
    uid = user_id or settings.default_user_id

    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.db.repositories.portfolio import PortfolioRepository

    async with AsyncSessionLocal() as session:
        repo = PortfolioRepository(session)
        snapshot = await repo.get_latest(uid)

    if not snapshot:
        click.echo(f"No portfolio snapshot found for user '{uid}'.")
        click.echo("Run: python -m investing_agent.cli sync-portfolio")
        sys.exit(1)

    click.echo(f"\nLatest snapshot: {snapshot.snapshot_date} (source: {snapshot.source})")
    click.echo(f"Total value : ₹{float(snapshot.total_value):,.2f}")
    click.echo(f"Unrealised  : ₹{float(snapshot.total_pnl):,.2f}")


@cli.command("sync-corporate-actions")
@click.argument("symbol")
def sync_corporate_actions(symbol: str) -> None:
    """Sync dividends/bonus/split/buyback/board-meetings for SYMBOL from NSE
    (+ BSE cross-check).

    \b
    Pipeline:
        NSE/BSE discover -> archive -> normalize -> cross-check verify -> persist
    """
    asyncio.run(_sync_corporate_actions(symbol))


async def _sync_corporate_actions(symbol: str) -> None:
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.ingestion.corporate_actions import (
        CorporateActionIngestionService,
    )

    async with AsyncSessionLocal() as session:
        service = CorporateActionIngestionService(session)
        try:
            result = await service.sync(symbol)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            click.echo(f"  ERROR: {exc}", err=True)
            sys.exit(1)
        finally:
            await service.aclose()

    click.echo(f"\nCorporate actions sync — {result.symbol}")
    click.echo(f"  Documents archived : {result.documents_archived}")
    click.echo(f"  New/revised rows   : {result.new_versions}")
    click.echo(f"  Unchanged (idempotent skip): {result.unchanged}")
    click.echo(f"  Skipped (unparseable)      : {result.skipped_unparseable}")
    click.echo(f"  Cross-source verified      : {result.verified_count}")
    for row in sorted(result.rows, key=lambda r: r.event_date, reverse=True)[:10]:
        click.echo(
            f"    {row.action_type:10s} event={row.event_date} ex={row.ex_date} "
            f"amt={row.amount} status={row.verification_status}"
        )


@cli.command("sync-financial-results")
@click.argument("symbol")
def sync_financial_results(symbol: str) -> None:
    """Sync quarterly financial results for SYMBOL from NSE.

    \b
    Pipeline:
        NSE discover -> archive -> normalize -> persist (versioned)
    """
    asyncio.run(_sync_financial_results(symbol))


async def _sync_financial_results(symbol: str) -> None:
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.ingestion.financial_results import (
        FinancialResultIngestionService,
    )

    async with AsyncSessionLocal() as session:
        service = FinancialResultIngestionService(session)
        try:
            result = await service.sync(symbol)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            click.echo(f"  ERROR: {exc}", err=True)
            sys.exit(1)
        finally:
            await service.aclose()

    click.echo(f"\nFinancial results sync — {result.symbol}")
    click.echo(f"  Documents archived : {result.documents_archived}")
    click.echo(f"  New/revised rows   : {result.new_versions}")
    click.echo(f"  Unchanged (idempotent skip): {result.unchanged}")
    click.echo(f"  Skipped (unparseable)      : {result.skipped_unparseable}")
    for row in sorted(result.rows, key=lambda r: r.result_date or "", reverse=True)[:8]:
        click.echo(
            f"    result_date={row.result_date} scope={row.statement_scope} "
            f"basis={row.reporting_basis} revenue={row.revenue} pat={row.pat} "
            f"status={row.verification_status}"
        )


@cli.command("sync-filings")
@click.argument("symbol")
def sync_filings(symbol: str) -> None:
    """Discover NSE announcements for SYMBOL and auto-archive investor
    presentations / concall transcripts (PDF or ZIP).

    \b
    Pipeline:
        NSE discover (all announcements, cheap) -> classify -> auto-archive
        allowlisted categories only -> hash -> archive -> deterministic PDF
        text cache. Everything else (annual reports, general announcements)
        stays a manual archive-document job — see cli.py module docstring.
    """
    asyncio.run(_sync_filings(symbol))


async def _sync_filings(symbol: str) -> None:
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.ingestion.filings import FilingIngestionService

    async with AsyncSessionLocal() as session:
        service = FilingIngestionService(session)
        try:
            result = await service.sync(symbol)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            click.echo(f"  ERROR: {exc}", err=True)
            sys.exit(1)
        finally:
            await service.aclose()

    click.echo(f"\nFilings sync — {result.symbol}")
    click.echo(f"  Announcements discovered : {result.announcements_discovered}")
    click.echo(f"  Documents archived       : {result.documents_archived}")
    click.echo(f"  Already archived (idempotent skip): {result.documents_already_archived}")
    click.echo(f"  ZIP children archived    : {result.zip_children_archived}")
    click.echo(f"  Download failures        : {result.download_failures}")
    if result.blocked:
        click.echo("  WARNING: source signaled an access block (403) — sync stopped early")
    for row in result.archived_documents[:10]:
        click.echo(f"    {row.id}  [{row.filing_type:20s}]  {row.title}")


@cli.command("sync-company-data")
@click.argument("symbol")
def sync_company_data(symbol: str) -> None:
    """Full company-intelligence sync for SYMBOL: corporate actions +
    financial results + filing discovery."""
    asyncio.run(_sync_company_data(symbol))


async def _sync_company_data(symbol: str) -> None:
    await _sync_corporate_actions(symbol)
    await _sync_financial_results(symbol)
    await _sync_filings(symbol)


@cli.command("sync-portfolio-companies")
@click.option("--user-id", default=None)
def sync_portfolio_companies(user_id: str | None) -> None:
    """Run sync-company-data for every symbol currently in the portfolio."""
    asyncio.run(_sync_portfolio_companies(user_id))


async def _sync_portfolio_companies(user_id: str | None) -> None:
    settings = get_settings()
    uid = user_id or settings.default_user_id

    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.db.repositories.portfolio import PortfolioRepository

    async with AsyncSessionLocal() as session:
        repo = PortfolioRepository(session)
        symbols = await repo.get_portfolio_symbols(uid)

    if not symbols:
        click.echo(f"No portfolio holdings found for user '{uid}'. Run sync-portfolio first.")
        return

    click.echo(f"Syncing company data for {len(symbols)} portfolio symbols: {', '.join(symbols)}")
    for symbol in symbols:
        click.echo(f"\n{'=' * 60}\n{symbol}\n{'=' * 60}")
        await _sync_corporate_actions(symbol)
        await _sync_financial_results(symbol)
        await _sync_filings(symbol)


# ── Phase 3B: primary-source document archival + structured entry ───────────
#
# Manual pipeline (no automated NSE/BSE document discovery yet — see
# research/provider_evaluation/REPORT.md §11): a human downloads a PDF,
# archives it with archive-document, reads it with extract-text, then
# transcribes structured facts with the record-* commands, always citing
# --source-document-id and --page/--quote. Every record-* command defaults
# to extraction_method=MANUAL / verification_status=UNVERIFIED; --verify
# flips to HUMAN_VERIFIED only on explicit human action, never automatically.

_FILING_TYPES = [
    "quarterly_result", "annual_report", "investor_presentation",
    "announcement", "concall_transcript", "order_contract", "other",
]
_DOCUMENT_TYPE_BY_EXTENSION = {
    ".pdf": "pdf", ".html": "html", ".htm": "html", ".xml": "xml",
    ".xbrl": "xbrl", ".json": "json", ".txt": "txt",
}


@cli.command("archive-document")
@click.argument("symbol")
@click.option("--file", "file_path", required=True, type=click.Path(exists=True, dir_okay=False),
              help="Path to the already-downloaded document")
@click.option("--filing-type", required=True, type=click.Choice(_FILING_TYPES))
@click.option("--title", required=True)
@click.option("--exchange", default="IR", type=click.Choice(["NSE", "BSE", "IR"]))
@click.option("--source-url", default=None)
def archive_document_cmd(
    symbol: str, file_path: str, filing_type: str, title: str, exchange: str,
    source_url: str | None,
) -> None:
    """Manually archive an already-downloaded document (PDF/HTML/XML/etc.)
    for SYMBOL. Works regardless of whether live NSE/BSE document discovery
    is available."""
    asyncio.run(_archive_document(symbol, file_path, filing_type, title, exchange, source_url))


async def _archive_document(
    symbol: str, file_path: str, filing_type: str, title: str, exchange: str,
    source_url: str | None,
) -> None:
    from pathlib import Path

    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.ingestion.common import archive_document, ensure_company
    from investing_agent.services.sources.interfaces import DiscoveredDocument

    path = Path(file_path)
    document_type = _DOCUMENT_TYPE_BY_EXTENSION.get(path.suffix.lower(), "pdf")
    content = path.read_bytes()

    async with AsyncSessionLocal() as session:
        company = await ensure_company(session, symbol)
        doc = DiscoveredDocument(
            company_symbol=company.symbol,
            exchange=exchange,
            source="manual",
            source_type="manual_upload",
            filing_type=filing_type,
            document_type=document_type,
            title=title,
            content=content,
            source_url=source_url or f"file://{path.resolve()}",
            fetched_at=datetime.now(UTC),
        )
        try:
            row, created = await archive_document(session, company, doc)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            click.echo(f"  ERROR: {exc}", err=True)
            sys.exit(1)

    click.echo(f"\n{'Archived' if created else 'Already archived (idempotent)'}: {row.title}")
    click.echo(f"  source_document_id: {row.id}")
    click.echo(f"  storage_path      : {row.storage_path}")
    click.echo(f"  content_hash      : {row.content_hash}")


@cli.command("show-filings")
@click.argument("symbol")
@click.option("--filing-type", default=None, type=click.Choice(_FILING_TYPES))
def show_filings_cmd(symbol: str, filing_type: str | None) -> None:
    """List archived documents for SYMBOL — find the source_document_id to
    cite in a record-* command."""
    asyncio.run(_show_filings(symbol, filing_type))


async def _show_filings(symbol: str, filing_type: str | None) -> None:
    from investing_agent.db.repositories.company import CompanyRepository
    from investing_agent.db.repositories.source_document import SourceDocumentRepository
    from investing_agent.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        company = await CompanyRepository(session).get_by_symbol(symbol.upper())
        if not company:
            click.echo(f"No company found for symbol '{symbol}'.")
            sys.exit(1)
        docs = await SourceDocumentRepository(session).list_by_company(company.id)

    if filing_type:
        docs = [d for d in docs if d.filing_type == filing_type]

    if not docs:
        click.echo(f"No archived documents for {symbol}.")
        return

    click.echo(f"\nArchived documents — {symbol} ({len(docs)})")
    for d in docs:
        published = d.published_at.date().isoformat() if d.published_at else "?"
        click.echo(f"  {d.id}  [{d.filing_type:20s}] {published}  {d.title}")


@cli.command("extract-text")
@click.argument("source_document_id")
@click.option("--page", type=int, default=None, help="Print only this page (1-indexed)")
def extract_text_cmd(source_document_id: str, page: int | None) -> None:
    """Print mechanically extracted text for an archived PDF, so a human can
    read the source before transcribing values. Cached on disk after the
    first extraction (see services.storage.write_text_cache)."""
    asyncio.run(_extract_text(source_document_id, page))


async def _extract_text(source_document_id: str, page: int | None) -> None:
    from investing_agent.db.models import SourceDocument
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.extraction.pdf_text import (
        deserialize_pages,
        extract_pdf_text,
        serialize_pages,
    )
    from investing_agent.services.storage import read_archive, read_text_cache, write_text_cache

    async with AsyncSessionLocal() as session:
        doc = await session.get(SourceDocument, uuid.UUID(source_document_id))

    if not doc:
        click.echo(f"No source document with id {source_document_id}.", err=True)
        sys.exit(1)
    if not doc.storage_path:
        click.echo("This document has no archived bytes (storage_path is empty).", err=True)
        sys.exit(1)
    if doc.document_type != "pdf":
        click.echo(
            f"Text extraction only supports pdf (document_type={doc.document_type!r}).", err=True
        )
        sys.exit(1)

    cached = read_text_cache(doc.storage_path)
    if cached is not None:
        pages = deserialize_pages(cached)
    else:
        content = read_archive(doc.storage_path)
        pages = extract_pdf_text(content)
        write_text_cache(doc.storage_path, serialize_pages(pages))

    selected = [p for p in pages if page is None or p.page_number == page]
    if page is not None and not selected:
        click.echo(f"Document has {len(pages)} page(s); page {page} does not exist.", err=True)
        sys.exit(1)

    for p in selected:
        click.echo(f"\n{'─' * 60}\nPage {p.page_number}\n{'─' * 60}")
        click.echo(p.text)


def _extraction_fields(
    source_document_id: str, page: int | None, quote: str | None,
    verify: bool, verified_by: str | None,
) -> dict:
    now = datetime.now(UTC)
    if verify and not verified_by:
        click.echo("  ERROR: --verify requires --verified-by", err=True)
        sys.exit(1)
    return dict(
        source_document_id=uuid.UUID(source_document_id),
        source_page=page,
        source_quote=quote,
        extracted_at=now,
        extraction_method="MANUAL",
        verification_status="HUMAN_VERIFIED" if verify else "UNVERIFIED",
        verified_at=now if verify else None,
        verified_by=verified_by if verify else None,
        source_type="manual_entry",
    )


async def _resolve_company(session, symbol: str):
    from investing_agent.services.ingestion.common import ensure_company

    return await ensure_company(session, symbol)


@cli.command("record-order-book")
@click.argument("symbol")
@click.option("--source-document-id", required=True)
@click.option("--as-of-date", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--value", "order_book_value", required=True, type=float)
@click.option("--currency", default="INR")
@click.option(
    "--unit-scale", default="UNRESOLVED",
    type=click.Choice(["LAKH", "CRORE", "ACTUAL", "UNRESOLVED"]),
)
@click.option("--segment", default=None)
@click.option("--book-to-bill", "book_to_bill_ratio", default=None, type=float)
@click.option("--execution-period", "expected_execution_period", default=None)
@click.option("--notes", default=None)
@click.option("--page", type=int, default=None)
@click.option("--quote", default=None)
@click.option("--verify", is_flag=True)
@click.option("--verified-by", default=None)
def record_order_book_cmd(symbol, source_document_id, as_of_date, order_book_value, currency,
                           unit_scale, segment, book_to_bill_ratio, expected_execution_period,
                           notes, page, quote, verify, verified_by) -> None:
    """Record a disclosed order-book value for SYMBOL as of a date."""
    asyncio.run(_record_order_book(
        symbol, source_document_id, as_of_date.date(), order_book_value, currency,
        unit_scale, segment, book_to_bill_ratio, expected_execution_period,
        notes, page, quote, verify, verified_by,
    ))


async def _record_order_book(symbol, source_document_id, as_of_date, order_book_value, currency,
                              unit_scale, segment, book_to_bill_ratio, expected_execution_period,
                              notes, page, quote, verify, verified_by) -> None:
    from investing_agent.db.repositories.company_research import OrderBookSnapshotRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.schemas.company_research import OrderBookSnapshotCreate

    async with AsyncSessionLocal() as session:
        company = await _resolve_company(session, symbol)
        data = OrderBookSnapshotCreate(
            company_id=company.id, symbol=company.symbol, as_of_date=as_of_date,
            order_book_value=order_book_value, currency=currency, unit_scale=unit_scale,
            segment=segment, book_to_bill_ratio=book_to_bill_ratio,
            expected_execution_period=expected_execution_period, notes=notes,
            **_extraction_fields(source_document_id, page, quote, verify, verified_by),
        )
        row = await OrderBookSnapshotRepository(session).create(data)
        await session.commit()

    click.echo(f"\nRecorded order book snapshot — {symbol} as of {as_of_date}")
    click.echo(f"  id: {row.id}  verification_status={row.verification_status}")


@cli.command("record-guidance")
@click.argument("symbol")
@click.option("--source-document-id", required=True)
@click.option("--fiscal-year", required=True, type=int)
@click.option("--guidance-type", required=True)
@click.option("--metric-label", required=True)
@click.option("--value-text", "guidance_value_text", required=True)
@click.option("--low", "guidance_low", default=None, type=float)
@click.option("--high", "guidance_high", default=None, type=float)
@click.option("--period-label", default=None)
@click.option("--given-by", default=None)
@click.option("--context", default=None)
@click.option("--page", type=int, default=None)
@click.option("--quote", default=None)
@click.option("--verify", is_flag=True)
@click.option("--verified-by", default=None)
def record_guidance_cmd(symbol, source_document_id, fiscal_year, guidance_type, metric_label,
                         guidance_value_text, guidance_low, guidance_high, period_label,
                         given_by, context, page, quote, verify, verified_by) -> None:
    """Record forward-looking management guidance for SYMBOL."""
    asyncio.run(_record_guidance(
        symbol, source_document_id, fiscal_year, guidance_type, metric_label,
        guidance_value_text, guidance_low, guidance_high, period_label,
        given_by, context, page, quote, verify, verified_by,
    ))


async def _record_guidance(symbol, source_document_id, fiscal_year, guidance_type, metric_label,
                            guidance_value_text, guidance_low, guidance_high, period_label,
                            given_by, context, page, quote, verify, verified_by) -> None:
    from investing_agent.db.repositories.company_research import ManagementGuidanceRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.schemas.company_research import ManagementGuidanceCreate

    async with AsyncSessionLocal() as session:
        company = await _resolve_company(session, symbol)
        data = ManagementGuidanceCreate(
            company_id=company.id, symbol=company.symbol, fiscal_year=fiscal_year,
            guidance_type=guidance_type, metric_label=metric_label,
            guidance_value_text=guidance_value_text, guidance_low=guidance_low,
            guidance_high=guidance_high, period_label=period_label, given_by=given_by,
            context=context,
            **_extraction_fields(source_document_id, page, quote, verify, verified_by),
        )
        row = await ManagementGuidanceRepository(session).create(data)
        await session.commit()

    click.echo(f"\nRecorded guidance — {symbol} FY{fiscal_year} {guidance_type}")
    click.echo(f"  id: {row.id}  verification_status={row.verification_status}")


@cli.command("record-segment-metric")
@click.argument("symbol")
@click.option("--source-document-id", required=True)
@click.option("--segment-name", required=True)
@click.option("--metric-type", required=True)
@click.option("--value", required=True, type=float)
@click.option("--period-id", default=None)
@click.option(
    "--unit-scale", default="UNRESOLVED",
    type=click.Choice(["LAKH", "CRORE", "ACTUAL", "UNRESOLVED"]),
)
@click.option("--currency", default="INR")
@click.option("--page", type=int, default=None)
@click.option("--quote", default=None)
@click.option("--verify", is_flag=True)
@click.option("--verified-by", default=None)
def record_segment_metric_cmd(symbol, source_document_id, segment_name, metric_type, value,
                               period_id, unit_scale, currency, page, quote, verify,
                               verified_by) -> None:
    """Record a segment-level (business-line/geography) metric for SYMBOL."""
    asyncio.run(_record_segment_metric(
        symbol, source_document_id, segment_name, metric_type, value,
        period_id, unit_scale, currency, page, quote, verify, verified_by,
    ))


async def _record_segment_metric(symbol, source_document_id, segment_name, metric_type, value,
                                  period_id, unit_scale, currency, page, quote, verify,
                                  verified_by) -> None:
    from investing_agent.db.repositories.company_research import SegmentMetricRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.schemas.company_research import SegmentMetricCreate

    async with AsyncSessionLocal() as session:
        company = await _resolve_company(session, symbol)
        data = SegmentMetricCreate(
            company_id=company.id, symbol=company.symbol,
            period_id=uuid.UUID(period_id) if period_id else None,
            segment_name=segment_name, metric_type=metric_type, value=value,
            unit_scale=unit_scale, currency=currency,
            **_extraction_fields(source_document_id, page, quote, verify, verified_by),
        )
        row = await SegmentMetricRepository(session).create(data)
        await session.commit()

    click.echo(f"\nRecorded segment metric — {symbol} {segment_name}/{metric_type}")
    click.echo(f"  id: {row.id}  verification_status={row.verification_status}")


@cli.command("record-operational-metric")
@click.argument("symbol")
@click.option("--source-document-id", required=True)
@click.option("--metric-name", required=True)
@click.option("--value", required=True, type=float)
@click.option("--unit", default=None)
@click.option("--as-of-date", default=None, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--period-id", default=None)
@click.option("--page", type=int, default=None)
@click.option("--quote", default=None)
@click.option("--verify", is_flag=True)
@click.option("--verified-by", default=None)
def record_operational_metric_cmd(symbol, source_document_id, metric_name, value, unit,
                                   as_of_date, period_id, page, quote, verify,
                                   verified_by) -> None:
    """Record a non-financial operating metric for SYMBOL."""
    asyncio.run(_record_operational_metric(
        symbol, source_document_id, metric_name, value, unit,
        as_of_date.date() if as_of_date else None, period_id, page, quote, verify, verified_by,
    ))


async def _record_operational_metric(symbol, source_document_id, metric_name, value, unit,
                                      as_of_date, period_id, page, quote, verify,
                                      verified_by) -> None:
    from investing_agent.db.repositories.company_research import OperationalMetricRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.schemas.company_research import OperationalMetricCreate

    async with AsyncSessionLocal() as session:
        company = await _resolve_company(session, symbol)
        data = OperationalMetricCreate(
            company_id=company.id, symbol=company.symbol,
            period_id=uuid.UUID(period_id) if period_id else None,
            metric_name=metric_name, value=value, unit=unit, as_of_date=as_of_date,
            **_extraction_fields(source_document_id, page, quote, verify, verified_by),
        )
        row = await OperationalMetricRepository(session).create(data)
        await session.commit()

    click.echo(f"\nRecorded operational metric — {symbol} {metric_name}")
    click.echo(f"  id: {row.id}  verification_status={row.verification_status}")


@cli.command("record-capacity-update")
@click.argument("symbol")
@click.option("--source-document-id", required=True)
@click.option("--update-type", required=True)
@click.option("--location", default=None)
@click.option("--capacity-before", default=None, type=float)
@click.option("--capacity-after", default=None, type=float)
@click.option("--unit", default=None)
@click.option("--announced-date", default=None, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--expected-completion", default=None)
@click.option("--description", default=None)
@click.option("--page", type=int, default=None)
@click.option("--quote", default=None)
@click.option("--verify", is_flag=True)
@click.option("--verified-by", default=None)
def record_capacity_update_cmd(symbol, source_document_id, update_type, location,
                                capacity_before, capacity_after, unit, announced_date,
                                expected_completion, description, page, quote, verify,
                                verified_by) -> None:
    """Record a manufacturing/production capacity change for SYMBOL."""
    asyncio.run(_record_capacity_update(
        symbol, source_document_id, update_type, location, capacity_before, capacity_after,
        unit, announced_date.date() if announced_date else None, expected_completion,
        description, page, quote, verify, verified_by,
    ))


async def _record_capacity_update(symbol, source_document_id, update_type, location,
                                   capacity_before, capacity_after, unit, announced_date,
                                   expected_completion, description, page, quote, verify,
                                   verified_by) -> None:
    from investing_agent.db.repositories.company_research import CapacityUpdateRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.schemas.company_research import CapacityUpdateCreate

    async with AsyncSessionLocal() as session:
        company = await _resolve_company(session, symbol)
        data = CapacityUpdateCreate(
            company_id=company.id, symbol=company.symbol, update_type=update_type,
            location=location, capacity_before=capacity_before, capacity_after=capacity_after,
            unit=unit, announced_date=announced_date, expected_completion=expected_completion,
            description=description,
            **_extraction_fields(source_document_id, page, quote, verify, verified_by),
        )
        row = await CapacityUpdateRepository(session).create(data)
        await session.commit()

    click.echo(f"\nRecorded capacity update — {symbol} {update_type}")
    click.echo(f"  id: {row.id}  verification_status={row.verification_status}")


@cli.command("record-commentary")
@click.argument("symbol")
@click.option("--source-document-id", required=True)
@click.option("--quote", required=True, help="The management quote itself (verbatim)")
@click.option("--speaker", default=None)
@click.option("--topic", default=None)
@click.option("--context", default=None)
@click.option("--period-id", default=None)
@click.option("--page", type=int, default=None)
@click.option("--verify", is_flag=True)
@click.option("--verified-by", default=None)
def record_commentary_cmd(symbol, source_document_id, quote, speaker, topic, context,
                           period_id, page, verify, verified_by) -> None:
    """Record a direct management quote/commentary for SYMBOL. The quote text
    itself is also used as the extraction evidence (source_quote)."""
    asyncio.run(_record_commentary(
        symbol, source_document_id, quote, speaker, topic, context, period_id, page,
        verify, verified_by,
    ))


async def _record_commentary(symbol, source_document_id, quote, speaker, topic, context,
                              period_id, page, verify, verified_by) -> None:
    from investing_agent.db.repositories.company_research import ManagementCommentaryRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.schemas.company_research import ManagementCommentaryCreate

    async with AsyncSessionLocal() as session:
        company = await _resolve_company(session, symbol)
        data = ManagementCommentaryCreate(
            company_id=company.id, symbol=company.symbol,
            period_id=uuid.UUID(period_id) if period_id else None,
            speaker=speaker, topic=topic, quote=quote, context=context,
            **_extraction_fields(source_document_id, page, quote, verify, verified_by),
        )
        row = await ManagementCommentaryRepository(session).create(data)
        await session.commit()

    click.echo(f"\nRecorded management commentary — {symbol} ({speaker or 'unknown speaker'})")
    click.echo(f"  id: {row.id}  verification_status={row.verification_status}")


# ── Phase 4A: news ingestion + research memory ───────────────────────────────
#
# Only livemint/economic_times are production-enabled here — Google News
# stays behind services/sources/google_news_source.py, never registered in
# _NEWS_SOURCES, pending the licensing decision (see db/models.py Phase 4A
# section header). Continuous polling (not one-shot ticker lookup) is what
# made LiveMint/ET useful in the bake-off; run sync-news on a schedule
# (cron/APScheduler later), not as a single ad hoc query.

_NEWS_SOURCES: dict[str, type] = {}


def _news_sources() -> dict[str, type]:
    if not _NEWS_SOURCES:
        from investing_agent.services.sources.economic_times_source import (
            EconomicTimesNewsSource,
        )
        from investing_agent.services.sources.livemint_source import LiveMintNewsSource

        _NEWS_SOURCES["livemint"] = LiveMintNewsSource
        _NEWS_SOURCES["economic_times"] = EconomicTimesNewsSource
    return _NEWS_SOURCES


@cli.command("sync-news")
@click.option(
    "--source", "source_name", default=None,
    type=click.Choice(["livemint", "economic_times"]),
    help="Sync only this source; omit to sync all production-enabled sources",
)
@click.option(
    "--company", "company_symbol", default=None,
    help="After syncing, print recent NewsEvents for this company symbol",
)
def sync_news_cmd(source_name: str | None, company_symbol: str | None) -> None:
    """Poll LiveMint / Economic Times RSS, dedup, match to companies, and
    cluster into NewsEvents. Google News is not wired here — see
    services/sources/google_news_source.py.

    \b
    Pipeline:
        RSS fetch -> exact/near-dup filter -> archive metadata -> company
        match (CompanyAlias) -> NewsEvent clustering
    """
    asyncio.run(_sync_news(source_name, company_symbol))


async def _sync_news(source_name: str | None, company_symbol: str | None) -> None:
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.ingestion.news import NewsIngestionService

    sources = _news_sources()
    names = [source_name] if source_name else list(sources.keys())

    for name in names:
        async with AsyncSessionLocal() as session:
            source = sources[name]()
            service = NewsIngestionService(session, source)
            try:
                result = await service.sync()
                await session.commit()
            except Exception as exc:
                await session.rollback()
                click.echo(f"  ERROR ({name}): {exc}", err=True)
                sys.exit(1)
            finally:
                await service.aclose()

        click.echo(f"\nNews sync — {name}")
        click.echo(f"  Discovered              : {result.items_discovered}")
        click.echo(f"  New items               : {result.items_created}")
        click.echo(f"  Duplicates (exact/near) : {result.items_duplicate}")
        click.echo(f"  Company links created   : {result.company_links_created}")
        click.echo(
            f"  Events created/extended : {result.events_created}/{result.events_extended}"
        )
        if result.blocked:
            click.echo("  WARNING: source signaled an access block — sync stopped early")
        if result.stale:
            click.echo("  WARNING: feed appears stale (newest item older than 48h)")

    if company_symbol:
        await _show_company_events(company_symbol)


async def _show_company_events(symbol: str) -> None:
    from investing_agent.db.repositories.company import CompanyRepository
    from investing_agent.db.repositories.news import NewsEventRepository
    from investing_agent.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        company = await CompanyRepository(session).get_by_symbol(symbol.upper())
        if not company:
            click.echo(f"\nNo company found for symbol '{symbol}'.")
            return
        events = await NewsEventRepository(session).list_by_company(company.id)

    click.echo(f"\nRecent NewsEvents — {symbol} ({len(events)})")
    for e in events[:15]:
        click.echo(
            f"  {e.id}  [{e.event_type:12s}] last_seen={e.last_seen_at.date()}  "
            f"{e.representative_headline}"
        )


@cli.command("record-research-note")
@click.argument("symbol")
@click.option("--text", required=True, help="The note content")
@click.option("--note-type", default="manual")
@click.option(
    "--effective-at", default=None, type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Defaults to now if omitted",
)
@click.option("--source-document-id", default=None)
@click.option("--news-event-id", default=None)
@click.option("--created-by", required=True)
def record_research_note_cmd(
    symbol: str, text: str, note_type: str, effective_at: datetime | None,
    source_document_id: str | None, news_event_id: str | None, created_by: str,
) -> None:
    """Record a manual research note for SYMBOL — preserves an observation
    even when it didn't come through automated ingestion."""
    asyncio.run(_record_research_note(
        symbol, text, note_type, effective_at, source_document_id, news_event_id, created_by,
    ))


async def _record_research_note(
    symbol: str, text: str, note_type: str, effective_at: datetime | None,
    source_document_id: str | None, news_event_id: str | None, created_by: str,
) -> None:
    from investing_agent.db.repositories.research_memory import ResearchNoteRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.schemas.research_memory import ResearchNoteCreate

    async with AsyncSessionLocal() as session:
        company = await _resolve_company(session, symbol)
        data = ResearchNoteCreate(
            company_id=company.id,
            note_type=note_type,
            text=text,
            effective_at=effective_at or datetime.now(UTC),
            source_document_id=uuid.UUID(source_document_id) if source_document_id else None,
            news_event_id=uuid.UUID(news_event_id) if news_event_id else None,
            created_by=created_by,
        )
        row = await ResearchNoteRepository(session).create(data)
        await session.commit()

    click.echo(f"\nRecorded research note — {symbol}")
    click.echo(f"  id: {row.id}  effective_at={row.effective_at}")


@cli.command("record-news-url")
@click.argument("symbol")
@click.option("--url", "source_url", required=True)
@click.option("--headline", required=True)
@click.option("--description", "feed_description", default=None)
@click.option("--publisher", default=None)
@click.option(
    "--published-at", default=None, type=click.DateTime(formats=["%Y-%m-%d"]),
)
def record_news_url_cmd(
    symbol: str, source_url: str, headline: str, feed_description: str | None,
    publisher: str | None, published_at: datetime | None,
) -> None:
    """Manually preserve a news article that RSS discovery missed, linked to
    SYMBOL. Stores only headline/description/URL metadata — never scraped
    article text (same constraint as automated ingestion)."""
    asyncio.run(_record_news_url(
        symbol, source_url, headline, feed_description, publisher, published_at,
    ))


async def _record_news_url(
    symbol: str, source_url: str, headline: str, feed_description: str | None,
    publisher: str | None, published_at: datetime | None,
) -> None:
    from decimal import Decimal

    from investing_agent.db.repositories.company_alias import CompanyAliasRepository
    from investing_agent.db.repositories.news import NewsCompanyLinkRepository, NewsItemRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.schemas.news import NewsCompanyLinkCreate, NewsItemCreate
    from investing_agent.services.dedup.news_dedup import compute_content_hash
    from investing_agent.services.ingestion.news import cluster_event
    from investing_agent.services.matching.company_matcher import CompanyMatcher

    async with AsyncSessionLocal() as session:
        company = await _resolve_company(session, symbol)
        item_repo = NewsItemRepository(session)
        content_hash = compute_content_hash("manual", headline)
        item, created = await item_repo.get_or_create(
            NewsItemCreate(
                headline=headline, feed_description=feed_description, publisher=publisher,
                source_name="manual", source_url=source_url,
                published_at=published_at, content_hash=content_hash, raw_metadata=None,
            )
        )

        link_repo = NewsCompanyLinkRepository(session)
        await link_repo.get_or_create(
            NewsCompanyLinkCreate(
                news_item_id=item.id, company_id=company.id,
                relevance_score=Decimal("1.00"), match_method="manual",
            )
        )

        # Also run automated matching for any OTHER companies mentioned, so
        # a manual entry participates in research-memory queries the same
        # way an RSS-discovered one does.
        alias_repo = CompanyAliasRepository(session)
        matcher = CompanyMatcher(await alias_repo.list_active())
        for match in matcher.match(f"{headline} {feed_description or ''}"):
            if match.company_id == company.id:
                continue
            await link_repo.get_or_create(
                NewsCompanyLinkCreate(
                    news_item_id=item.id, company_id=match.company_id,
                    relevance_score=match.relevance_score, match_method=match.match_method,
                )
            )

        event, _event_created = await cluster_event(session, item, company.id)
        await session.commit()

    click.echo(f"\n{'Recorded' if created else 'Already recorded (idempotent)'} news item — {symbol}")
    click.echo(f"  news_item_id : {item.id}")
    click.echo(f"  news_event_id: {event.id}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
