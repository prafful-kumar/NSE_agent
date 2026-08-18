from __future__ import annotations

"""CLI entry point.

Usage:
    python -m investing_agent.cli sync-portfolio
    python -m investing_agent.cli ping-broker
    python -m investing_agent.cli portfolio-status

Run 'python -m investing_agent.cli --help' for all commands.
"""

import asyncio
import re
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal

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


@cli.command("verify-financial-result")
@click.argument("symbol")
@click.option("--quarter", required=True, help="Target quarter, e.g. Q3FY25")
@click.option(
    "--scope", default="CONSOLIDATED",
    type=click.Choice(["STANDALONE", "CONSOLIDATED", "UNRESOLVED"]),
)
@click.option(
    "--basis", default="QUARTER", type=click.Choice(["QUARTER", "YTD", "ANNUAL"])
)
@click.option(
    "--source-document-id", required=True,
    help="Archived primary document (NSE filing PDF preferred, IR PDF, BSE fallback — "
    "never a secondary financial website) — see show-filings",
)
@click.option("--revenue", default=None, type=Decimal, help="Revenue as reported in the document")
@click.option("--pat", default=None, type=Decimal, help="PAT as reported in the document")
@click.option(
    "--eps-diluted", default=None, type=Decimal, help="Diluted EPS as reported in the document"
)
def verify_financial_result_cmd(
    symbol: str,
    quarter: str,
    scope: str,
    basis: str,
    source_document_id: str,
    revenue: Decimal | None,
    pat: Decimal | None,
    eps_diluted: Decimal | None,
) -> None:
    """Cross-check the stored FinancialResult for SYMBOL's QUARTER against
    values read from an archived primary document, and promote
    verification_status to "verified" (or "disputed" on a mismatch).

    Reads --revenue/--pat/--eps-diluted from you (the reviewer, reading the
    primary document's own text) — this command never re-derives them from
    the document itself. Provide at least one.
    """
    if revenue is None and pat is None and eps_diluted is None:
        click.echo(
            "  ERROR: provide at least one of --revenue/--pat/--eps-diluted", err=True
        )
        sys.exit(1)
    try:
        fiscal_year, quarter_label = _parse_quarter_arg(quarter)
    except ValueError as exc:
        click.echo(f"  {exc}", err=True)
        sys.exit(1)
    asyncio.run(
        _verify_financial_result(
            symbol, fiscal_year, quarter_label, scope, basis,
            source_document_id, revenue, pat, eps_diluted,
        )
    )


async def _verify_financial_result(
    symbol: str,
    fiscal_year: int,
    quarter_label: str,
    scope: str,
    basis: str,
    source_document_id: str,
    revenue: Decimal | None,
    pat: Decimal | None,
    eps_diluted: Decimal | None,
) -> None:
    from investing_agent.db.repositories.financial import (
        FinancialPeriodRepository,
        FinancialResultRepository,
    )
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.normalization import quarter_label_to_period_end
    from investing_agent.services.verification import verify_financial_result_manual

    period_end = quarter_label_to_period_end(fiscal_year, quarter_label)
    label = f"{quarter_label}FY{str(fiscal_year)[2:]}"

    async with AsyncSessionLocal() as session:
        company = await _resolve_company(session, symbol)
        period_repo = FinancialPeriodRepository(session)
        period = await period_repo.get_or_create(
            company.id, "quarter", fiscal_year, quarter_label, period_end, label
        )
        result_repo = FinancialResultRepository(session)
        candidates = await result_repo.list_latest_by_period(period.id, scope, basis)
        if not candidates:
            click.echo(
                f"  ERROR: no {scope}/{basis} FinancialResult found for {symbol} {label}", err=True
            )
            sys.exit(1)
        if len(candidates) > 1:
            click.echo(
                f"  ERROR: {len(candidates)} candidate rows found (multiple source_types) — "
                "not yet supported by this command",
                err=True,
            )
            sys.exit(1)
        target = candidates[0]

        try:
            outcome = await verify_financial_result_manual(
                session,
                result_id=target.id,
                source_document_id=uuid.UUID(source_document_id),
                reported_revenue=revenue,
                reported_pat=pat,
                reported_eps_diluted=eps_diluted,
            )
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            click.echo(f"  ERROR: {exc}", err=True)
            sys.exit(1)

    if outcome.matched:
        click.echo(f"\nVERIFIED — {symbol} {label} ({scope}/{basis})")
        click.echo(f"  Checked fields: {', '.join(outcome.checked_fields)}")
    else:
        click.echo(f"\nDISPUTED — {symbol} {label} ({scope}/{basis})")
        for m in outcome.mismatches:
            click.echo(f"  {m}")
    if outcome.timestamp_upgraded:
        click.echo(
            f"  Timestamp precision upgraded to EXACT (available_at={outcome.result.available_at})"
        )


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


@cli.command("interpret-events")
@click.option("--company", "company_symbol", default=None, help="Limit to one company symbol")
@click.option("--since-days", default=90, type=int, help="How far back to look for NewsEvents")
@click.option(
    "--use-llm/--no-llm", default=True,
    help="Fall back to Claude for events no deterministic rule matches (requires "
    "ANTHROPIC_API_KEY); --no-llm runs the rule layer only",
)
def interpret_events_cmd(company_symbol: str | None, since_days: int, use_llm: bool) -> None:
    """Classify recent NewsEvents into candidate impact assessments —
    deterministic rules first, LLM fallback for anything ambiguous. Always
    writes review_status="pending"; nothing here touches catalysts,
    risk_observations, or thesis_changes directly. Review with
    list-interpretations / review-interpretation.
    """
    asyncio.run(_interpret_events(company_symbol, since_days, use_llm))


async def _interpret_events(company_symbol: str | None, since_days: int, use_llm: bool) -> None:
    from datetime import timedelta

    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.interpretation.service import InterpretationService

    settings = get_settings()
    llm_interpreter = None
    if use_llm:
        if settings.anthropic_api_key:
            from investing_agent.services.interpretation.llm_interpreter import (
                ClaudeEventInterpreter,
            )

            llm_interpreter = ClaudeEventInterpreter()
        else:
            click.echo(
                "  Note: ANTHROPIC_API_KEY not set — running deterministic rules only.",
            )

    since = datetime.now(UTC) - timedelta(days=since_days)

    async with AsyncSessionLocal() as session:
        company_id = None
        if company_symbol:
            company = await _resolve_company(session, company_symbol)
            company_id = company.id

        service = InterpretationService(session, llm_interpreter=llm_interpreter)
        try:
            result = await service.interpret_pending(company_id=company_id, since=since)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            click.echo(f"  ERROR: {exc}", err=True)
            sys.exit(1)

    click.echo("\nEvent interpretation run")
    click.echo(f"  Events considered         : {result.events_considered}")
    click.echo(f"  Interpreted (deterministic): {result.interpreted_deterministic}")
    click.echo(f"  Interpreted (LLM)         : {result.interpreted_llm}")
    click.echo(f"  Already interpreted       : {result.skipped_already_interpreted}")
    click.echo(f"  No interpretation produced: {result.skipped_no_interpretation}")


@cli.command("list-interpretations")
@click.option("--company", "company_symbol", default=None, help="Limit to one company symbol")
@click.option(
    "--status", "review_status", default="pending",
    type=click.Choice(["pending", "accepted", "rejected", "edited", "all"]),
)
def list_interpretations_cmd(company_symbol: str | None, review_status: str) -> None:
    """List EventInterpretation candidates awaiting (or past) review."""
    asyncio.run(_list_interpretations(company_symbol, review_status))


async def _list_interpretations(company_symbol: str | None, review_status: str) -> None:
    from investing_agent.db.repositories.interpretation import EventInterpretationRepository
    from investing_agent.db.repositories.news import NewsEventRepository
    from investing_agent.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        interp_repo = EventInterpretationRepository(session)
        event_repo = NewsEventRepository(session)

        company_id = None
        if company_symbol:
            company = await _resolve_company(session, company_symbol)
            company_id = company.id

        if review_status == "pending":
            rows = await interp_repo.list_pending(company_id=company_id)
        elif company_id is not None:
            rows = await interp_repo.list_by_company(company_id)
            if review_status != "all":
                rows = [r for r in rows if r.review_status == review_status]
        else:
            click.echo("  --company is required unless --status pending", err=True)
            sys.exit(1)

        click.echo(f"\nEvent interpretations ({review_status}) — {len(rows)}")
        for row in rows:
            event = await event_repo.get(row.news_event_id)
            headline = event.representative_headline if event else "(event not found)"
            dims = ", ".join(
                f"{dim}:{v['direction']}/{v['magnitude']}"
                for dim, v in row.impact_classification.items()
            )
            click.echo(
                f"  {row.id}  [{row.extraction_method:12s} conf={row.confidence}]  {headline}"
            )
            click.echo(f"      {dims}")


@cli.command("review-interpretation")
@click.argument("interpretation_id")
@click.option("--accept", "accept", is_flag=True)
@click.option("--reject", "reject", is_flag=True)
@click.option("--reviewed-by", required=True)
def review_interpretation_cmd(interpretation_id: str, accept: bool, reject: bool, reviewed_by: str) -> None:
    """Accept or reject an EventInterpretation candidate. --accept creates
    the corresponding Catalyst/RiskObservation/ThesisChange row(s) from the
    candidate payload and links them via resulting_*_id; --reject just
    records the decision. Exactly one of --accept/--reject is required.
    """
    if accept == reject:
        click.echo("  Specify exactly one of --accept or --reject", err=True)
        sys.exit(1)
    asyncio.run(_review_interpretation(interpretation_id, accept, reject, reviewed_by))


async def _review_interpretation(
    interpretation_id: str, accept: bool, reject: bool, reviewed_by: str
) -> None:
    from investing_agent.db.repositories.interpretation import EventInterpretationRepository
    from investing_agent.db.repositories.research_memory import (
        CatalystRepository,
        RiskObservationRepository,
        ThesisChangeRepository,
    )
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.schemas.research_memory import (
        CatalystCreate,
        RiskObservationCreate,
        ThesisChangeCreate,
    )

    async with AsyncSessionLocal() as session:
        interp_repo = EventInterpretationRepository(session)
        interpretation = await interp_repo.get(uuid.UUID(interpretation_id))
        if interpretation is None:
            click.echo(f"  No EventInterpretation found for id {interpretation_id}", err=True)
            sys.exit(1)
        if interpretation.review_status != "pending":
            click.echo(
                f"  EventInterpretation {interpretation_id} is already "
                f"'{interpretation.review_status}' — not re-reviewing.",
                err=True,
            )
            sys.exit(1)

        if reject:
            await interp_repo.mark_rejected(interpretation, reviewed_by=reviewed_by)
            await session.commit()
            click.echo(f"\nRejected interpretation {interpretation_id}")
            return

        resulting_catalyst_id = None
        resulting_risk_observation_id = None
        resulting_thesis_change_id = None

        if interpretation.candidate_catalyst:
            catalyst = await CatalystRepository(session).create(
                CatalystCreate(
                    company_id=interpretation.company_id,
                    news_event_id=interpretation.news_event_id,
                    **interpretation.candidate_catalyst,
                )
            )
            resulting_catalyst_id = catalyst.id

        if interpretation.candidate_risk:
            risk = await RiskObservationRepository(session).create(
                RiskObservationCreate(
                    company_id=interpretation.company_id,
                    news_event_id=interpretation.news_event_id,
                    **interpretation.candidate_risk,
                )
            )
            resulting_risk_observation_id = risk.id

        if interpretation.candidate_thesis_change:
            thesis_change = await ThesisChangeRepository(session).create(
                ThesisChangeCreate(
                    company_id=interpretation.company_id,
                    evidence_news_event_id=interpretation.news_event_id,
                    effective_at=datetime.now(UTC),
                    **interpretation.candidate_thesis_change,
                )
            )
            resulting_thesis_change_id = thesis_change.id

        await interp_repo.mark_accepted(
            interpretation,
            reviewed_by=reviewed_by,
            resulting_catalyst_id=resulting_catalyst_id,
            resulting_risk_observation_id=resulting_risk_observation_id,
            resulting_thesis_change_id=resulting_thesis_change_id,
        )
        await session.commit()

    click.echo(f"\nAccepted interpretation {interpretation_id}")
    if resulting_catalyst_id:
        click.echo(f"  catalyst created         : {resulting_catalyst_id}")
    if resulting_risk_observation_id:
        click.echo(f"  risk observation created : {resulting_risk_observation_id}")
    if resulting_thesis_change_id:
        click.echo(f"  thesis change created    : {resulting_thesis_change_id}")


_QUARTER_RE = re.compile(r"^(Q[1-4])FY(\d{2,4})$", re.IGNORECASE)


def _parse_quarter_arg(quarter: str) -> tuple[int, str]:
    """Parses e.g. 'Q2FY26' -> (2026, 'Q2'). Raises ValueError on bad format."""
    match = _QUARTER_RE.match(quarter)
    if not match:
        raise ValueError(f"Invalid --quarter format: {quarter!r} (expected e.g. Q2FY26)")
    quarter_label = match.group(1).upper()
    fy_raw = match.group(2)
    fiscal_year = int(fy_raw) if len(fy_raw) == 4 else 2000 + int(fy_raw)
    return fiscal_year, quarter_label


@cli.command("generate-estimate")
@click.argument("symbol")
@click.option("--quarter", required=True, help="Target quarter, e.g. Q2FY26")
@click.option(
    "--cutoff-at", default=None,
    type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]),
    help="Point-in-time cutoff for the feature snapshot (defaults to now)",
)
@click.option("--model-version", default="deterministic-v1")
@click.option(
    "--use-llm/--no-llm", default=True,
    help="Add an LLM narrator for qualitative assumptions (requires ANTHROPIC_API_KEY, "
    "never influences any numeric field); --no-llm runs the deterministic estimator only",
)
def generate_estimate_cmd(
    symbol: str, quarter: str, cutoff_at: datetime | None, model_version: str, use_llm: bool
) -> None:
    """Generate an earnings estimate for SYMBOL's target QUARTER (e.g. Q2FY26).

    Every numeric field (revenue/EBITDA-margin/PAT/EPS low/base/high,
    confidence) is computed deterministically from historical financials,
    order book, guidance, and segment/operational data. The optional LLM
    narrator only ever adds qualitative assumption text — see
    services/estimation/narrator.py for why it structurally cannot touch a
    number.
    """
    try:
        fiscal_year, quarter_label = _parse_quarter_arg(quarter)
    except ValueError as exc:
        click.echo(f"  {exc}", err=True)
        sys.exit(1)
    asyncio.run(_generate_estimate(symbol, fiscal_year, quarter_label, cutoff_at, model_version, use_llm))


async def _generate_estimate(
    symbol: str,
    fiscal_year: int,
    quarter_label: str,
    cutoff_at: datetime | None,
    model_version: str,
    use_llm: bool,
) -> None:
    from investing_agent.db.repositories.financial import FinancialPeriodRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.estimation.service import EstimationService
    from investing_agent.services.normalization import quarter_label_to_period_end

    settings = get_settings()
    narrator = None
    if use_llm:
        if settings.anthropic_api_key:
            from investing_agent.services.estimation.narrator import ClaudeEstimateNarrator

            narrator = ClaudeEstimateNarrator()
        else:
            click.echo(
                "  Note: ANTHROPIC_API_KEY not set — running deterministic estimator only.",
            )

    resolved_cutoff = cutoff_at.replace(tzinfo=UTC) if cutoff_at else datetime.now(UTC)
    period_end = quarter_label_to_period_end(fiscal_year, quarter_label)
    label = f"{quarter_label}FY{str(fiscal_year)[2:]}"

    async with AsyncSessionLocal() as session:
        company = await _resolve_company(session, symbol)
        period = await FinancialPeriodRepository(session).get_or_create(
            company.id, "quarter", fiscal_year, quarter_label, period_end, label
        )
        service = EstimationService(session, narrator=narrator)
        try:
            run = await service.generate_estimate(
                company_id=company.id,
                financial_period_id=period.id,
                cutoff_at=resolved_cutoff,
                model_version=model_version,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            click.echo(f"  ERROR: {exc}", err=True)
            sys.exit(1)

    click.echo(f"\nEstimate {run.id} for {symbol} {label} (model {run.model_version})")
    click.echo(f"  Revenue        : {run.revenue_low} / {run.revenue_base} / {run.revenue_high}")
    click.echo(
        f"  EBITDA margin %: {run.ebitda_margin_low} / {run.ebitda_margin_base} / "
        f"{run.ebitda_margin_high}"
    )
    click.echo(f"  PAT            : {run.pat_low} / {run.pat_base} / {run.pat_high}")
    click.echo(f"  EPS            : {run.eps_low} / {run.eps_base} / {run.eps_high}")
    click.echo(f"  Confidence     : {run.confidence}")


@cli.command("list-estimates")
@click.argument("symbol")
def list_estimates_cmd(symbol: str) -> None:
    """List EstimateRun history for SYMBOL, most recent first."""
    asyncio.run(_list_estimates(symbol))


async def _list_estimates(symbol: str) -> None:
    from investing_agent.db.repositories.estimation import EstimateRunRepository
    from investing_agent.db.repositories.financial import FinancialPeriodRepository
    from investing_agent.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        company = await _resolve_company(session, symbol)
        rows = await EstimateRunRepository(session).list_by_company(company.id)
        period_repo = FinancialPeriodRepository(session)

        click.echo(f"\nEstimate runs for {symbol} — {len(rows)}")
        for row in rows:
            period = await period_repo.get(row.financial_period_id)
            label = period.label if period else str(row.financial_period_id)
            click.echo(
                f"  {row.id}  {label}  [{row.model_version}]  "
                f"rev={row.revenue_base} pat={row.pat_base} eps={row.eps_base} "
                f"conf={row.confidence}"
            )


@cli.command("show-estimate")
@click.argument("estimate_id")
def show_estimate_cmd(estimate_id: str) -> None:
    """Show full detail for one EstimateRun, including assumptions."""
    asyncio.run(_show_estimate(estimate_id))


async def _show_estimate(estimate_id: str) -> None:
    from investing_agent.db.repositories.estimation import EstimateRunRepository
    from investing_agent.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        run = await EstimateRunRepository(session).get(uuid.UUID(estimate_id))
        if run is None:
            click.echo(f"  No EstimateRun found for id {estimate_id}", err=True)
            sys.exit(1)

    click.echo(f"\nEstimateRun {run.id}")
    click.echo(f"  Model version  : {run.model_version}")
    click.echo(f"  Cutoff at      : {run.cutoff_at}")
    click.echo(f"  Revenue        : {run.revenue_low} / {run.revenue_base} / {run.revenue_high}")
    click.echo(
        f"  EBITDA margin %: {run.ebitda_margin_low} / {run.ebitda_margin_base} / "
        f"{run.ebitda_margin_high}"
    )
    click.echo(f"  PAT            : {run.pat_low} / {run.pat_base} / {run.pat_high}")
    click.echo(f"  EPS            : {run.eps_low} / {run.eps_base} / {run.eps_high}")
    click.echo(f"  Confidence     : {run.confidence}")
    click.echo("  Assumptions:")
    for a in run.assumptions:
        click.echo(f"    [{a['source']}] {a['text']}")


@cli.command("run-backtest")
@click.option("--symbol", default=None, help="Restrict to a single company symbol (default: all companies)")
@click.option("--model-version", default="deterministic-v1")
def run_backtest_cmd(symbol: str | None, model_version: str) -> None:
    """Re-run the estimator against every historical quarter that already
    has a known actual result, scoring each estimate's accuracy now that the
    real outcome is known.

    Each historical estimate is generated with a cutoff_at derived from the
    earliest available_at among that quarter's actual FinancialResult rows
    (minus one second) — never a guessed offset — so it is structurally
    impossible for the estimate to have seen the result it's scored against.
    """
    asyncio.run(_run_backtest(symbol, model_version))


async def _run_backtest(symbol: str | None, model_version: str) -> None:
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.backtesting.runner import run_backtest

    async with AsyncSessionLocal() as session:
        company_id = None
        if symbol:
            company = await _resolve_company(session, symbol)
            company_id = company.id
        try:
            result = await run_backtest(session, company_id=company_id, model_version=model_version)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            click.echo(f"  ERROR: {exc}", err=True)
            sys.exit(1)

    click.echo(
        f"\nBacktest complete — {len(result.scores)} scores persisted (model {model_version})"
    )
    click.echo("\nPer-period diagnostics:")
    for d in result.diagnostics:
        click.echo(f"\n  {d.period_label}")
        click.echo(f"    actual_available_at : {d.actual_available_at}")
        click.echo(f"    actual_ingested_at  : {d.actual_ingested_at}")
        click.echo(f"    cutoff_at           : {d.cutoff_at}")
        click.echo(f"    verified history    : {d.verified_history_count}")
        click.echo(f"    unverified history  : {d.unverified_history_count}")
        click.echo(f"    estimate            : {d.estimate_status}")
        if d.reason:
            click.echo(f"    reason              : {d.reason}")

    scored = [s for s in result.scores if s.status == "SCORED"]
    if scored:
        click.echo("\nScored:")
    for s in scored:
        click.echo(
            f"  {s.id}  period={s.financial_period_id}  "
            f"rev_err={s.revenue_error_pct}%  pat_err={s.pat_error_pct}%  "
            f"eps_err={s.eps_error_pct}%  surprise={s.surprise_direction}  "
            f"in_band(pat)={s.within_band_pat}  conf={s.confidence_bucket}"
        )


@cli.command("backtest-report")
@click.option("--symbol", default=None, help="Restrict to a single company symbol")
@click.option("--sector", default=None, help="Restrict to a single sector")
@click.option("--model-version", default=None, help="Restrict to a single model version")
def backtest_report_cmd(symbol: str | None, sector: str | None, model_version: str | None) -> None:
    """Aggregate BacktestScore accuracy: MAE/MAPE per metric, EBITDA-margin
    error, within-band hit rate, surprise-direction distribution,
    growth-direction accuracy, confidence calibration, and breakdowns by
    company/sector/model version."""
    asyncio.run(_backtest_report(symbol, sector, model_version))


def _print_summary(summary: dict) -> None:
    click.echo(f"  Scorable / unscorable: {summary['n'] - summary['unscorable_n']} / {summary['unscorable_n']}")
    click.echo(f"  Revenue MAPE      : {summary['revenue_mape_pct']}% (n={summary['revenue_mape_n']})")
    click.echo(f"  PAT MAPE          : {summary['pat_mape_pct']}% (n={summary['pat_mape_n']})")
    click.echo(f"  EPS MAPE          : {summary['eps_mape_pct']}% (n={summary['eps_mape_n']})")
    click.echo(f"  EBITDA margin MAE : {summary['margin_mae_bps']} bps (n={summary['margin_mae_n']})")
    click.echo(
        f"  Within-band hit   : revenue={summary['revenue_within_band_pct']}% "
        f"pat={summary['pat_within_band_pct']}% eps={summary['eps_within_band_pct']}%"
    )
    click.echo(
        f"  Growth-direction accuracy: {summary['growth_direction_accuracy_pct']}% "
        f"(n={summary['growth_direction_n']})"
    )
    dist = summary["surprise_direction_distribution_pct"]
    if dist:
        click.echo("  Surprise direction: " + " | ".join(f"{k}={v:.1f}%" for k, v in dist.items()))


async def _backtest_report(symbol: str | None, sector: str | None, model_version: str | None) -> None:
    from investing_agent.db.repositories.backtesting import BacktestScoreRepository
    from investing_agent.db.repositories.company import CompanyRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.schemas.backtesting import BacktestScoreRead
    from investing_agent.services.backtesting.report import group_and_summarize, summarize

    async with AsyncSessionLocal() as session:
        companies_by_id = {c.id: c for c in await CompanyRepository(session).list()}
        rows = await BacktestScoreRepository(session).list_all()

        if symbol:
            company = await _resolve_company(session, symbol)
            rows = [r for r in rows if r.company_id == company.id]
        if model_version:
            rows = [r for r in rows if r.model_version == model_version]
        if sector:
            rows = [
                r for r in rows
                if companies_by_id.get(r.company_id) and companies_by_id[r.company_id].sector == sector
            ]

        scores = [BacktestScoreRead.model_validate(r) for r in rows]

    if not scores:
        click.echo("\nNo backtest scores found matching filters.")
        return

    overall = summarize(scores)
    click.echo(f"\nBacktest report — {overall['n']} scores")
    _print_summary(overall)

    click.echo("\nConfidence calibration:")
    by_conf = group_and_summarize(scores, lambda s: s.confidence_bucket or "unknown")
    for bucket in ("low", "medium", "high", "unknown"):
        if bucket in by_conf:
            b = by_conf[bucket]
            click.echo(
                f"  {bucket:8s} n={b['n']:<4d} pat_within_band={b['pat_within_band_pct']}%"
            )

    click.echo("\nBy company:")
    by_company = group_and_summarize(
        scores,
        lambda s: companies_by_id[s.company_id].symbol if s.company_id in companies_by_id else str(s.company_id),
    )
    for key, b in by_company.items():
        click.echo(f"  {key:12s} n={b['n']:<4d} pat_mape={b['pat_mape_pct']}% eps_mape={b['eps_mape_pct']}%")

    click.echo("\nBy sector:")
    by_sector = group_and_summarize(
        scores,
        lambda s: (companies_by_id[s.company_id].sector if s.company_id in companies_by_id else None) or "unknown",
    )
    for key, b in by_sector.items():
        click.echo(f"  {key:12s} n={b['n']:<4d} pat_mape={b['pat_mape_pct']}%")

    click.echo("\nBy model version:")
    by_model = group_and_summarize(scores, lambda s: s.model_version)
    for key, b in by_model.items():
        click.echo(f"  {key:24s} n={b['n']:<4d} pat_mape={b['pat_mape_pct']}%")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
