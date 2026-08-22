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


@cli.command("sync-result-filings")
@click.argument("symbol")
@click.option(
    "--since",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    required=True,
    help="Inclusive issuer filing date (YYYY-MM-DD); records without a timestamp are skipped.",
)
def sync_result_filings(symbol: str, since: datetime) -> None:
    """Archive recent NSE financial-result PDFs/ZIPs for SYMBOL only.

    This command archives primary attachments and their metadata only.  It
    never creates or verifies FinancialResult facts; use the existing manual
    verification workflow after inspecting the archived document.
    """
    asyncio.run(_sync_result_filings(symbol, since.replace(tzinfo=UTC)))


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


async def _sync_result_filings(symbol: str, since: datetime) -> None:
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.ingestion.filings import FilingIngestionService

    async with AsyncSessionLocal() as session:
        service = FilingIngestionService(session)
        try:
            result = await service.sync(
                symbol, since=since, filing_types=frozenset({"quarterly_result"})
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            click.echo(f"  ERROR: {exc}", err=True)
            sys.exit(1)
        finally:
            await service.aclose()

    click.echo(f"\nFinancial-result filing sync — {result.symbol}")
    click.echo(f"  Since (issuer timestamp)  : {since.isoformat()}")
    click.echo(f"  Announcements discovered  : {result.announcements_discovered}")
    click.echo(f"  Documents archived        : {result.documents_archived}")
    click.echo(f"  Already archived          : {result.documents_already_archived}")
    click.echo(f"  ZIP children archived     : {result.zip_children_archived}")
    click.echo(f"  Download failures         : {result.download_failures}")
    if result.blocked:
        click.echo("  WARNING: source signaled an access block (403) — sync stopped early")
    for row in result.archived_documents[:10]:
        click.echo(f"    {row.id}  [{row.document_type}]  {row.title}")


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


@cli.command("record-historical-financial-result")
@click.argument("symbol")
@click.option("--quarter", required=True, help="Target quarter, e.g. Q3FY22")
@click.option(
    "--scope", default="STANDALONE",
    type=click.Choice(["STANDALONE", "CONSOLIDATED", "UNRESOLVED"]),
)
@click.option(
    "--basis", default="QUARTER", type=click.Choice(["QUARTER", "YTD", "ANNUAL"])
)
@click.option(
    "--source-document-id", required=True,
    help="Archived primary document this was transcribed from — see show-filings",
)
@click.option("--source-page", type=int, default=None)
@click.option("--source-quote", default=None, help="Short excerpt copied from the P&L table")
@click.option("--is-audited/--unaudited", default=True)
@click.option(
    "--unit-scale", default="LAKH", type=click.Choice(["LAKH", "CRORE", "ACTUAL", "UNRESOLVED"])
)
@click.option("--revenue", default=None, type=Decimal, help="Total income, as disclosed")
@click.option("--pbt", default=None, type=Decimal)
@click.option("--pat", default=None, type=Decimal)
@click.option("--eps-basic", default=None, type=Decimal)
@click.option("--eps-diluted", default=None, type=Decimal)
@click.option("--tax-expense", default=None, type=Decimal)
@click.option("--changes-in-inventories", default=None, type=Decimal)
@click.option("--employee-cost", default=None, type=Decimal)
@click.option("--finance-cost", default=None, type=Decimal)
@click.option("--other-expenses", default=None, type=Decimal)
def record_historical_financial_result_cmd(
    symbol: str,
    quarter: str,
    scope: str,
    basis: str,
    source_document_id: str,
    source_page: int | None,
    source_quote: str | None,
    is_audited: bool,
    unit_scale: str,
    revenue: Decimal | None,
    pbt: Decimal | None,
    pat: Decimal | None,
    eps_basic: Decimal | None,
    eps_diluted: Decimal | None,
    tax_expense: Decimal | None,
    changes_in_inventories: Decimal | None,
    employee_cost: Decimal | None,
    finance_cost: Decimal | None,
    other_expenses: Decimal | None,
) -> None:
    """Manually transcribe a historical quarterly result for SYMBOL's QUARTER
    from an archived primary document (older than the structured NSE
    results-comparison API's 5-quarter window).

    Every figure must come from you having actually read --source-document-id
    (see show-filings / extract-text) — this command never re-derives them.
    The row is created already verification_status="verified", since the
    primary document itself is the evidence.
    """
    if revenue is None and pbt is None and pat is None:
        click.echo("  ERROR: provide at least --revenue/--pbt/--pat", err=True)
        sys.exit(1)
    try:
        fiscal_year, quarter_label = _parse_quarter_arg(quarter)
    except ValueError as exc:
        click.echo(f"  {exc}", err=True)
        sys.exit(1)
    asyncio.run(
        _record_historical_financial_result(
            symbol, fiscal_year, quarter_label, scope, basis,
            source_document_id, source_page, source_quote, is_audited, unit_scale,
            revenue, pbt, pat, eps_basic, eps_diluted,
            tax_expense, changes_in_inventories, employee_cost, finance_cost, other_expenses,
        )
    )


async def _record_historical_financial_result(
    symbol: str,
    fiscal_year: int,
    quarter_label: str,
    scope: str,
    basis: str,
    source_document_id: str,
    source_page: int | None,
    source_quote: str | None,
    is_audited: bool,
    unit_scale: str,
    revenue: Decimal | None,
    pbt: Decimal | None,
    pat: Decimal | None,
    eps_basic: Decimal | None,
    eps_diluted: Decimal | None,
    tax_expense: Decimal | None,
    changes_in_inventories: Decimal | None,
    employee_cost: Decimal | None,
    finance_cost: Decimal | None,
    other_expenses: Decimal | None,
) -> None:
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.ingestion.historical_financials import (
        HistoricalFinancialTranscriptionError,
        record_historical_financial_result,
    )
    from investing_agent.services.normalization import quarter_label_to_period_end

    period_end = quarter_label_to_period_end(fiscal_year, quarter_label)
    label = f"{quarter_label}FY{str(fiscal_year)[2:]}"

    async with AsyncSessionLocal() as session:
        company = await _resolve_company(session, symbol)
        try:
            outcome = await record_historical_financial_result(
                session,
                company_id=company.id,
                symbol=symbol.upper(),
                fiscal_year=fiscal_year,
                quarter=quarter_label,
                period_end=period_end,
                period_start=None,
                period_label=label,
                statement_scope=scope,
                reporting_basis=basis,
                is_audited=is_audited,
                result_date=None,
                unit_scale=unit_scale,
                source_document_id=uuid.UUID(source_document_id),
                source_page=source_page,
                source_quote=source_quote,
                revenue=revenue,
                pbt=pbt,
                pat=pat,
                eps_basic=eps_basic,
                eps_diluted=eps_diluted,
                tax_expense=tax_expense,
                changes_in_inventories=changes_in_inventories,
                employee_cost=employee_cost,
                finance_cost=finance_cost,
                other_expenses=other_expenses,
            )
            await session.commit()
        except HistoricalFinancialTranscriptionError as exc:
            await session.rollback()
            click.echo(f"  ERROR: {exc}", err=True)
            sys.exit(1)

    verb = "Created" if outcome.was_new_version else "Unchanged (idempotent)"
    click.echo(
        f"\n{verb} — {symbol} {label} ({scope}/{basis}) "
        f"id={outcome.row.id} version={outcome.row.version} "
        f"verification_status={outcome.row.verification_status} "
        f"available_at={outcome.row.available_at}"
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
    from investing_agent.db.repositories.financial import FinancialPeriodRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.schemas.backtesting import BacktestScoreRead
    from investing_agent.services.backtesting.report import group_and_summarize, summarize

    async with AsyncSessionLocal() as session:
        companies_by_id = {c.id: c for c in await CompanyRepository(session).list()}
        periods_by_id = {p.id: p for p in await FinancialPeriodRepository(session).list()}
        all_rows = await BacktestScoreRepository(session).list_all()

        # A backtest rerun always inserts a new BacktestScore rather than
        # overwriting (preserves full history across model_version changes —
        # see BacktestScore's docstring). For reporting "current" accuracy,
        # only the most recent score per (company, period, model_version)
        # should count; list_all() is already ordered by created_at desc, so
        # first-seen-wins per key gives the latest.
        latest_by_key: dict[tuple, object] = {}
        for r in all_rows:
            key = (r.company_id, r.financial_period_id, r.model_version)
            latest_by_key.setdefault(key, r)
        rows = list(latest_by_key.values())

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

    def _transition_label(s: "BacktestScoreRead") -> str:
        period = periods_by_id.get(s.financial_period_id)
        if period is None or not period.quarter:
            return "unknown"
        prior_quarter = {"Q1": "Q4", "Q2": "Q1", "Q3": "Q2", "Q4": "Q3"}.get(period.quarter, "?")
        return f"{prior_quarter}->{period.quarter}"

    click.echo("\nBy fiscal-quarter transition (descriptive only — no fitted coefficients):")
    by_transition = group_and_summarize(scores, _transition_label)
    for key in ("Q4->Q1", "Q1->Q2", "Q2->Q3", "Q3->Q4", "unknown"):
        if key in by_transition:
            b = by_transition[key]
            click.echo(
                f"  {key:10s} n={b['n']:<4d} rev_mape={b['revenue_mape_pct']}% "
                f"pat_mape={b['pat_mape_pct']}% pat_within_band={b['pat_within_band_pct']}%"
            )


@cli.command("financial-seasonality")
@click.option("--scope", default="STANDALONE", type=click.Choice(["STANDALONE", "CONSOLIDATED", "UNRESOLVED"]))
@click.argument("symbol")
def financial_seasonality_cmd(symbol: str, scope: str) -> None:
    """Descriptive (non-fitted) margin/tax seasonality diagnostics for
    SYMBOL: average PBT margin %, PAT margin %, and effective tax rate %
    per fiscal quarter (Q1-Q4), computed directly from verified quarterly
    FinancialResult rows. For analyst review ahead of any estimator
    change -- never fits a coefficient, never feeds the estimator."""
    asyncio.run(_financial_seasonality(symbol, scope))


async def _financial_seasonality(symbol: str, scope: str) -> None:
    from sqlalchemy import select

    from investing_agent.db.models import FinancialPeriod, FinancialResult
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.diagnostics import QuarterlyFinancialPoint, compute_quarter_seasonality

    async with AsyncSessionLocal() as session:
        company = await _resolve_company(session, symbol)

        rows = await session.execute(
            select(FinancialResult, FinancialPeriod)
            .join(FinancialPeriod, FinancialResult.period_id == FinancialPeriod.id)
            .where(
                FinancialResult.company_id == company.id,
                FinancialResult.statement_scope == scope,
                FinancialResult.reporting_basis == "QUARTER",
                FinancialResult.is_latest.is_(True),
                FinancialResult.verification_status == "verified",
            )
        )

        points: list[QuarterlyFinancialPoint] = []
        for result, period in rows.all():
            if period.quarter is None:
                continue
            tax_expense_raw = (result.other_metrics or {}).get("tax_expense")
            points.append(
                QuarterlyFinancialPoint(
                    quarter=period.quarter,
                    fiscal_year=period.fiscal_year,
                    period_label=period.label,
                    revenue=result.revenue,
                    pbt=result.pbt,
                    pat=result.pat,
                    tax_expense=Decimal(tax_expense_raw) if tax_expense_raw is not None else None,
                )
            )

    if not points:
        click.echo(f"\nNo verified {scope} quarterly results found for {symbol}.")
        return

    by_quarter = compute_quarter_seasonality(points)
    click.echo(f"\nMargin/tax seasonality — {symbol} ({scope}, n={len(points)} verified quarters)")
    click.echo("Descriptive only — no fitted coefficients:\n")
    for quarter in ("Q1", "Q2", "Q3", "Q4"):
        if quarter in by_quarter:
            b = by_quarter[quarter]
            click.echo(
                f"  {quarter}   n={b['n']:<3d} "
                f"avg_pbt_margin={b['avg_pbt_margin_pct']}%  "
                f"avg_pat_margin={b['avg_pat_margin_pct']}%  "
                f"avg_effective_tax_rate={b['avg_effective_tax_rate_pct']}%"
            )


# ── Broker-account historical import (Phase 6A) ─────────────────────────────────

@cli.command("record-broker-account")
@click.option("--user-id", default=None, help="User ID (defaults to DEFAULT_USER_ID from .env)")
@click.option(
    "--broker", required=True, type=click.Choice(["ZERODHA", "INDMONEY"]),
)
@click.option(
    "--account-label", required=True,
    help="Distinguishes multiple accounts at the same broker, e.g. 'primary'",
)
def record_broker_account_cmd(user_id: str | None, broker: str, account_label: str) -> None:
    """Get-or-create a BrokerAccount. strategy_profile is fixed by broker
    (ZERODHA -> LONG_TERM, INDMONEY -> MEDIUM_TERM), not user-configurable."""
    asyncio.run(_record_broker_account(user_id, broker, account_label))


async def _record_broker_account(user_id: str | None, broker: str, account_label: str) -> None:
    from investing_agent.db.repositories.broker_history import BrokerAccountRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.schemas.broker_history import BrokerAccountCreate

    settings = get_settings()
    resolved_user_id = user_id or settings.default_user_id

    async with AsyncSessionLocal() as session:
        repo = BrokerAccountRepository(session)
        account, created = await repo.get_or_create(
            BrokerAccountCreate(user_id=resolved_user_id, broker=broker, account_label=account_label)
        )
        await session.commit()

    verb = "Created" if created else "Already exists"
    click.echo(
        f"\n{verb} — {broker} account '{account_label}' for user={resolved_user_id} "
        f"id={account.id} strategy_profile={account.strategy_profile}"
    )


@cli.command("import-broker-statement")
@click.option("--user-id", default=None, help="User ID (defaults to DEFAULT_USER_ID from .env)")
@click.option("--broker", required=True, type=click.Choice(["ZERODHA", "INDMONEY"]))
@click.option("--account-label", required=True)
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False))
def import_broker_statement_cmd(
    user_id: str | None, broker: str, account_label: str, file_path: str
) -> None:
    """Import one exported statement FILE_PATH for a broker account.

    Idempotent: re-running with the same file is a no-op; re-running with a
    different file that overlaps in date range (e.g. Zerodha Console
    tradebook exports, capped at 365 days per download) merges safely via
    per-trade dedupe keys rather than duplicating.
    """
    asyncio.run(_import_broker_statement(user_id, broker, account_label, file_path))


async def _import_broker_statement(
    user_id: str | None, broker: str, account_label: str, file_path: str
) -> None:
    from pathlib import Path

    from investing_agent.db.repositories.broker_history import BrokerAccountRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.ingestion.broker_history import get_importer
    from investing_agent.services.ingestion.broker_history.import_service import (
        import_statement_file,
    )

    settings = get_settings()
    resolved_user_id = user_id or settings.default_user_id

    try:
        importer = get_importer(broker)
    except KeyError as exc:
        click.echo(f"  ERROR: {exc}", err=True)
        sys.exit(1)

    async with AsyncSessionLocal() as session:
        account_repo = BrokerAccountRepository(session)
        account = await account_repo.get_by_label(resolved_user_id, broker, account_label)
        if account is None:
            click.echo(
                f"  ERROR: no BrokerAccount for user={resolved_user_id} broker={broker} "
                f"account_label={account_label} — run record-broker-account first.",
                err=True,
            )
            sys.exit(1)

        try:
            run = await import_statement_file(
                session, broker_account_id=account.id, importer=importer, file_path=Path(file_path)
            )
            await session.commit()
        except Exception as exc:
            # import_statement_file already wrote a FAILED HistoricalImportRun
            # row (for auditability, e.g. via broker-import-report) before
            # re-raising -- commit to keep that record rather than discard it.
            await session.commit()
            click.echo(f"  ERROR: import failed — {exc}", err=True)
            sys.exit(1)

    click.echo(
        f"\n{run.status} — {file_path}\n"
        f"  run_id={run.id} source_type={run.source_type}\n"
        f"  trades: +{run.trades_created} created, {run.trades_skipped_duplicate} already existed\n"
        f"  cash_flows: +{run.cash_flows_created} created, {run.cash_flows_skipped_duplicate} already existed\n"
        f"  dividends: +{run.dividends_created} created, {run.dividends_skipped_duplicate} already existed\n"
        f"  date_range: {run.date_range_start} .. {run.date_range_end}"
    )


@cli.command("broker-import-report")
@click.option("--user-id", default=None, help="User ID (defaults to DEFAULT_USER_ID from .env)")
@click.option("--broker", required=True, type=click.Choice(["ZERODHA", "INDMONEY"]))
@click.option("--account-label", required=True)
def broker_import_report_cmd(user_id: str | None, broker: str, account_label: str) -> None:
    """List every HistoricalImportRun for a broker account, most recent first."""
    asyncio.run(_broker_import_report(user_id, broker, account_label))


async def _broker_import_report(user_id: str | None, broker: str, account_label: str) -> None:
    from sqlalchemy import select

    from investing_agent.db.models import HistoricalImportRun
    from investing_agent.db.repositories.broker_history import BrokerAccountRepository
    from investing_agent.db.session import AsyncSessionLocal

    settings = get_settings()
    resolved_user_id = user_id or settings.default_user_id

    async with AsyncSessionLocal() as session:
        account_repo = BrokerAccountRepository(session)
        account = await account_repo.get_by_label(resolved_user_id, broker, account_label)
        if account is None:
            click.echo(
                f"  No BrokerAccount for user={resolved_user_id} broker={broker} "
                f"account_label={account_label}."
            )
            return

        result = await session.execute(
            select(HistoricalImportRun)
            .where(HistoricalImportRun.broker_account_id == account.id)
            .order_by(HistoricalImportRun.created_at.desc())
        )
        runs = list(result.scalars().all())

    if not runs:
        click.echo(f"\nNo import runs yet for {broker} '{account_label}'.")
        return

    click.echo(f"\nImport runs — {broker} '{account_label}' ({len(runs)} total)")
    for run in runs:
        click.echo(
            f"  [{run.status:7s}] {run.created_at.date()}  {run.file_name}  "
            f"trades+{run.trades_created}/{run.trades_skipped_duplicate}dup  "
            f"cash+{run.cash_flows_created}/{run.cash_flows_skipped_duplicate}dup  "
            f"div+{run.dividends_created}/{run.dividends_skipped_duplicate}dup"
            + (f"  ERROR: {run.error_message}" if run.error_message else "")
        )


# ── Portfolio reconstruction (Phase 6B) ─────────────────────────────────────

@cli.command("reconstruct-portfolio")
@click.option("--user-id", default=None, help="User ID (defaults to DEFAULT_USER_ID from .env)")
@click.option("--broker", required=True, type=click.Choice(["ZERODHA", "INDMONEY"]))
@click.option("--account-label", required=True)
@click.option("--as-of", default=None, help="YYYY-MM-DD (defaults to today)")
def reconstruct_portfolio_cmd(
    user_id: str | None, broker: str, account_label: str, as_of: str | None
) -> None:
    """Replay trades+cash_flows to answer 'what did my portfolio look like
    on date X'. Pure read -- does not write to historical_position_lots
    (use rebuild-position-lots for that)."""
    asyncio.run(_reconstruct_portfolio(user_id, broker, account_label, as_of))


async def _reconstruct_portfolio(
    user_id: str | None, broker: str, account_label: str, as_of: str | None
) -> None:
    from datetime import date as date_cls

    from investing_agent.db.repositories.broker_history import BrokerAccountRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.reconstruction.service import get_portfolio_as_of

    settings = get_settings()
    resolved_user_id = user_id or settings.default_user_id
    as_of_date = date_cls.fromisoformat(as_of) if as_of else datetime.now(UTC).date()

    async with AsyncSessionLocal() as session:
        account_repo = BrokerAccountRepository(session)
        account = await account_repo.get_by_label(resolved_user_id, broker, account_label)
        if account is None:
            click.echo(
                f"  ERROR: no BrokerAccount for user={resolved_user_id} broker={broker} "
                f"account_label={account_label}.",
                err=True,
            )
            sys.exit(1)

        snapshot = await get_portfolio_as_of(
            session, broker_account_id=account.id, as_of_date=as_of_date
        )

    click.echo(
        f"\nPortfolio as of {snapshot.as_of_date} — {broker} '{account_label}' "
        f"(strategy_profile={snapshot.strategy_profile})"
    )
    click.echo(
        f"  cash_balance_partial={snapshot.cash_balance_partial}  "
        f"[{snapshot.cash_balance_caveat}]"
    )
    click.echo(
        f"  invested_capital_total={snapshot.invested_capital_total}  "
        f"realized_pnl_cumulative_total={snapshot.realized_pnl_cumulative_total}  "
        f"unrealized_pnl_total={snapshot.unrealized_pnl_total}"
    )
    click.echo(f"\n  positions ({len(snapshot.positions)}):")
    for p in snapshot.positions:
        flag = "  [CORPORATE ACTION SUSPECTED]" if p.corporate_action_flag else ""
        click.echo(
            f"    {p.symbol:15s} qty={p.quantity_held:>12}  avg_cost={p.average_cost:>10}  "
            f"invested={p.invested_capital:>12}  realized_pnl={p.realized_pnl_cumulative:>12}{flag}"
        )
    if snapshot.corporate_action_adjustments:
        click.echo(f"\n  corporate_action_adjustments ({len(snapshot.corporate_action_adjustments)}):")
        for a in snapshot.corporate_action_adjustments:
            click.echo(
                f"    {a.symbol} {a.event_type} {a.event_date}: "
                f"{a.old_quantity}@{a.old_cost_per_share} -> {a.new_quantity}@{a.adjusted_cost_per_share} "
                f"(ratio={a.ratio_old}:{a.ratio_new}, source={a.source})"
            )
    if snapshot.warnings:
        click.echo(f"\n  warnings ({len(snapshot.warnings)}):")
        for w in snapshot.warnings:
            click.echo(f"    - {w}")


@cli.command("rebuild-position-lots")
@click.option("--user-id", default=None, help="User ID (defaults to DEFAULT_USER_ID from .env)")
@click.option("--broker", required=True, type=click.Choice(["ZERODHA", "INDMONEY"]))
@click.option("--account-label", required=True)
def rebuild_position_lots_cmd(user_id: str | None, broker: str, account_label: str) -> None:
    """Full-history FIFO replay, wholesale-replacing historical_position_lots
    for this account (see HistoricalPositionLot's own docstring)."""
    asyncio.run(_rebuild_position_lots(user_id, broker, account_label))


async def _rebuild_position_lots(user_id: str | None, broker: str, account_label: str) -> None:
    from investing_agent.db.repositories.broker_history import BrokerAccountRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.reconstruction.service import reconstruct_and_persist

    settings = get_settings()
    resolved_user_id = user_id or settings.default_user_id

    async with AsyncSessionLocal() as session:
        account_repo = BrokerAccountRepository(session)
        account = await account_repo.get_by_label(resolved_user_id, broker, account_label)
        if account is None:
            click.echo(
                f"  ERROR: no BrokerAccount for user={resolved_user_id} broker={broker} "
                f"account_label={account_label}.",
                err=True,
            )
            sys.exit(1)

        result = await reconstruct_and_persist(session, broker_account_id=account.id)
        await session.commit()

    click.echo(
        f"\nRebuilt position lots — {broker} '{account_label}' as of {result.as_of_date}\n"
        f"  lots_written={result.lots_written}  "
        f"symbols_with_open_positions={result.symbols_with_open_positions}  "
        f"warnings={len(result.warnings)}"
    )


@cli.command("reconcile-portfolio")
@click.option("--user-id", default=None, help="User ID (defaults to DEFAULT_USER_ID from .env)")
@click.option("--broker", required=True, type=click.Choice(["ZERODHA", "INDMONEY"]))
@click.option("--account-label", required=True)
@click.option(
    "--statements-dir", required=True,
    type=click.Path(exists=True, file_okay=False),
    help="Directory containing pnl-*.xlsx / holdings-*.xlsx exports",
)
def reconcile_portfolio_cmd(
    user_id: str | None, broker: str, account_label: str, statements_dir: str
) -> None:
    """Cross-check the FIFO reconstruction against Zerodha's own P&L /
    holdings exports. Exits non-zero if overall_status is DIVERGENT."""
    asyncio.run(_reconcile_portfolio(user_id, broker, account_label, statements_dir))


async def _reconcile_portfolio(
    user_id: str | None, broker: str, account_label: str, statements_dir: str
) -> None:
    from pathlib import Path

    from investing_agent.db.repositories.broker_history import BrokerAccountRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.reconstruction.reconciliation import reconcile_account

    settings = get_settings()
    resolved_user_id = user_id or settings.default_user_id

    async with AsyncSessionLocal() as session:
        account_repo = BrokerAccountRepository(session)
        account = await account_repo.get_by_label(resolved_user_id, broker, account_label)
        if account is None:
            click.echo(
                f"  ERROR: no BrokerAccount for user={resolved_user_id} broker={broker} "
                f"account_label={account_label}.",
                err=True,
            )
            sys.exit(1)

        statement_files = sorted(Path(statements_dir).glob("pnl-*.xlsx")) + sorted(
            Path(statements_dir).glob("holdings-*.xlsx")
        )
        report = await reconcile_account(
            session, broker_account_id=account.id, statement_files=statement_files
        )

    click.echo(
        f"\nReconciliation — {broker} '{account_label}' [{report.overall_status}]\n"
        f"  total_symbols_checked={report.total_symbols_checked}"
    )
    q = report.quality_score
    click.echo(
        f"  quality_score: holdings_qty={q.holdings_quantity_match_pct}%  "
        f"holdings_avg_cost={q.holdings_avg_cost_match_pct}%  "
        f"realized_pnl={q.realized_pnl_match_pct}%  "
        f"unresolved_symbols={q.unresolved_symbol_count}"
    )
    if report.expected_gaps_corporate_action:
        click.echo(
            f"  expected_gaps_corporate_action ({len(report.expected_gaps_corporate_action)}): "
            f"{', '.join(report.expected_gaps_corporate_action)}"
        )
    if report.divergent_symbols:
        click.echo(f"  divergent_symbols ({len(report.divergent_symbols)}): {', '.join(report.divergent_symbols)}")
        for d in report.row_diffs:
            click.echo(
                f"    [{d.statement_file}] {d.symbol} {d.field}: expected={d.expected} "
                f"actual={d.actual} delta={d.delta} ({d.likely_cause})"
            )
    if report.warnings:
        click.echo(f"  warnings: {report.warnings}")

    if report.overall_status == "DIVERGENT":
        sys.exit(1)


@cli.command("record-corporate-action")
@click.argument("symbol")
@click.option("--event-type", required=True, type=click.Choice(["SPLIT", "BONUS"]))
@click.option("--event-date", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--ratio-old", required=True, type=float, help="e.g. 1 in a 1:10 split")
@click.option("--ratio-new", required=True, type=float, help="e.g. 10 in a 1:10 split")
@click.option("--source", required=True, help="Provenance note, e.g. an NSE circular URL or description")
@click.option("--source-url", default=None)
@click.option("--notes", default=None)
def record_corporate_action_cmd(
    symbol: str,
    event_type: str,
    event_date: datetime,
    ratio_old: float,
    ratio_new: float,
    source: str,
    source_url: str | None,
    notes: str | None,
) -> None:
    """Record a stock split/bonus so FIFO reconstruction applies it to open
    lots at event_date (see services/reconstruction/fifo.py). Backed by the
    existing corporate_actions table (action_type split|bonus), not a new
    one -- upsert-versioned, so re-running with the same symbol/date/type
    is idempotent."""
    asyncio.run(
        _record_corporate_action(
            symbol, event_type, event_date.date(), ratio_old, ratio_new, source, source_url, notes
        )
    )


async def _record_corporate_action(
    symbol: str,
    event_type: str,
    event_date,
    ratio_old: float,
    ratio_new: float,
    source: str,
    source_url: str | None,
    notes: str | None,
) -> None:
    from decimal import Decimal

    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.reconstruction.corporate_actions import (
        record_corporate_action_event,
    )

    async with AsyncSessionLocal() as session:
        row, created = await record_corporate_action_event(
            session,
            symbol=symbol,
            event_type=event_type,  # type: ignore[arg-type]
            event_date=event_date,
            ratio_new=Decimal(str(ratio_new)),
            ratio_old=Decimal(str(ratio_old)),
            source=source,
            source_url=source_url,
            notes=notes,
        )
        await session.commit()

    verb = "Recorded" if created else "Already up to date:"
    click.echo(
        f"{verb} {symbol} {event_type} ratio={ratio_old}:{ratio_new} on {event_date} "
        f"(id={row.id}, version={row.version})"
    )


@cli.command("record-opening-position-adjustment")
@click.option("--user-id", default=None, help="User ID (defaults to DEFAULT_USER_ID from .env)")
@click.option("--broker", required=True, type=click.Choice(["ZERODHA", "INDMONEY"]))
@click.option("--account-label", required=True)
@click.argument("symbol")
@click.option("--opening-date", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--quantity", required=True, type=float)
@click.option("--cost-price", required=True, type=float)
@click.option("--source", required=True, help='e.g. "ZERODHA_PNL_RECONCILIATION"')
@click.option("--confidence", required=True, type=click.Choice(["LOW", "MEDIUM", "HIGH"]))
@click.option("--reason", required=True, help='e.g. "MISSING_TRADE_HISTORY"')
@click.option("--notes", default=None)
def record_opening_position_adjustment_cmd(
    user_id: str | None,
    broker: str,
    account_label: str,
    symbol: str,
    opening_date: datetime,
    quantity: float,
    cost_price: float,
    source: str,
    confidence: str,
    reason: str,
    notes: str | None,
) -> None:
    """Record a reconciliation-derived synthetic opening lot for a symbol
    whose real acquisition trade is missing from the tradebook (e.g. a
    SELL with no prior BUY) -- resolves the oversell at its source instead
    of leaving a zero-cost synthetic lot. Never fabricated silently:
    source/confidence/reason are mandatory and always surfaced in
    reconstruction warnings."""
    asyncio.run(
        _record_opening_position_adjustment(
            user_id, broker, account_label, symbol, opening_date.date(),
            quantity, cost_price, source, confidence, reason, notes,
        )
    )


async def _record_opening_position_adjustment(
    user_id: str | None,
    broker: str,
    account_label: str,
    symbol: str,
    opening_date,
    quantity: float,
    cost_price: float,
    source: str,
    confidence: str,
    reason: str,
    notes: str | None,
) -> None:
    from decimal import Decimal

    from investing_agent.db.repositories.broker_history import BrokerAccountRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.reconstruction.corporate_actions import (
        record_opening_position_adjustment,
    )

    settings = get_settings()
    resolved_user_id = user_id or settings.default_user_id

    async with AsyncSessionLocal() as session:
        account_repo = BrokerAccountRepository(session)
        account = await account_repo.get_by_label(resolved_user_id, broker, account_label)
        if account is None:
            click.echo(
                f"  ERROR: no BrokerAccount for user={resolved_user_id} broker={broker} "
                f"account_label={account_label}.",
                err=True,
            )
            sys.exit(1)

        row, created = await record_opening_position_adjustment(
            session,
            broker_account_id=account.id,
            symbol=symbol,
            opening_date=opening_date,
            quantity=Decimal(str(quantity)),
            cost_price=Decimal(str(cost_price)),
            source=source,
            confidence=confidence,  # type: ignore[arg-type]
            reason=reason,
            notes=notes,
        )
        await session.commit()

    verb = "Recorded" if created else "Updated"
    click.echo(
        f"{verb} opening-position adjustment: {symbol} qty={quantity} cost_price={cost_price} "
        f"on {opening_date} (id={row.id}, confidence={confidence}, reason={reason})"
    )


# ── Historical market prices (Phase 6C.0) ───────────────────────────────────

@cli.command("sync-daily-prices")
@click.argument("symbol")
@click.option("--start-date", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--end-date", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
def sync_daily_prices_cmd(symbol: str, start_date: datetime, end_date: datetime) -> None:
    """Fetch + archive + persist raw (unadjusted) NSE bhavcopy daily OHLCV
    for SYMBOL over [--start-date, --end-date]. Idempotent: re-running is a
    no-op for dates already synced."""
    asyncio.run(_sync_daily_prices(symbol, start_date.date(), end_date.date()))


async def _sync_daily_prices(symbol: str, start_date, end_date) -> None:
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.prices.ingestion import sync_daily_prices
    from investing_agent.services.prices.nse_bhavcopy import NSEBhavcopyHistoricalPriceProvider

    provider = NSEBhavcopyHistoricalPriceProvider()
    try:
        async with AsyncSessionLocal() as session:
            summary = await sync_daily_prices(session, symbol, start_date, end_date, provider)
            await session.commit()
    finally:
        await provider.aclose()

    click.echo(
        f"\nsync-daily-prices {symbol} {start_date} .. {end_date}\n"
        f"  dates_checked={summary.dates_checked} dates_no_data={summary.dates_no_data}\n"
        f"  rows: +{summary.rows_created} created, {summary.rows_skipped_duplicate} already existed"
    )
    if summary.errors:
        click.echo(f"  errors ({len(summary.errors)}):")
        for err in summary.errors:
            click.echo(f"    {err}")


@cli.command("sync-benchmark-prices")
@click.option("--benchmark-code", required=True, type=click.Choice(["NIFTY_50", "NIFTY_50_TRI"]))
@click.option("--start-date", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--end-date", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
def sync_benchmark_prices_cmd(
    benchmark_code: str, start_date: datetime, end_date: datetime
) -> None:
    """Fetch + archive + persist raw NSE Indices daily OHLC for
    --benchmark-code over [--start-date, --end-date]. Idempotent."""
    asyncio.run(_sync_benchmark_prices(benchmark_code, start_date.date(), end_date.date()))


async def _sync_benchmark_prices(benchmark_code: str, start_date, end_date) -> None:
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.prices.ingestion import sync_benchmark_prices
    from investing_agent.services.prices.nse_indices import NSEIndicesBenchmarkPriceProvider
    from investing_agent.services.prices.nifty_tri import NiftyIndicesTRIBenchmarkPriceProvider

    provider = (
        NiftyIndicesTRIBenchmarkPriceProvider()
        if benchmark_code == "NIFTY_50_TRI"
        else NSEIndicesBenchmarkPriceProvider(benchmark_code)
    )
    try:
        async with AsyncSessionLocal() as session:
            summary = await sync_benchmark_prices(
                session, benchmark_code, start_date, end_date, provider
            )
            await session.commit()
    finally:
        await provider.aclose()

    click.echo(
        f"\nsync-benchmark-prices {benchmark_code} {start_date} .. {end_date}\n"
        f"  dates_checked={summary.dates_checked} dates_no_data={summary.dates_no_data}\n"
        f"  rows: +{summary.rows_created} created, {summary.rows_skipped_duplicate} already existed"
    )
    if summary.errors:
        click.echo(f"  errors ({len(summary.errors)}):")
        for err in summary.errors:
            click.echo(f"    {err}")


@cli.command("show-daily-prices")
@click.argument("symbol")
@click.option("--start-date", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--end-date", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
def show_daily_prices_cmd(symbol: str, start_date: datetime, end_date: datetime) -> None:
    """List persisted daily_prices rows for SYMBOL in a date range (for
    manual verification against the source file)."""
    asyncio.run(_show_daily_prices(symbol, start_date.date(), end_date.date()))


async def _show_daily_prices(symbol: str, start_date, end_date) -> None:
    from investing_agent.db.repositories.prices import DailyPriceRepository
    from investing_agent.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        rows = await DailyPriceRepository(session).list_between(symbol.upper(), start_date, end_date)

    if not rows:
        click.echo(f"\nNo daily_prices rows for {symbol} between {start_date} and {end_date}.")
        return

    click.echo(f"\n{symbol} daily prices ({len(rows)} rows)")
    for row in rows:
        click.echo(
            f"  {row.trading_date}  O={row.open} H={row.high} L={row.low} C={row.close} "
            f"V={row.volume}  [{row.adjustment_status}]"
        )


@cli.command("walk-forward-run")
@click.option("--user-id", default=None, help="User ID (defaults to DEFAULT_USER_ID from .env)")
@click.option("--broker", required=True, type=click.Choice(["ZERODHA", "INDMONEY"]))
@click.option("--account-label", required=True)
@click.option(
    "--position",
    "positions",
    required=True,
    multiple=True,
    help="SYMBOL:YYYY-MM-DD, repeatable — one walk-forward decision date per symbol",
)
@click.option(
    "--horizons", default="1,3,6,12", help="Comma-separated month horizons (default: 1,3,6,12)"
)
@click.option("--model-version", default="walkforward-v1")
def walk_forward_run_cmd(
    user_id: str | None,
    broker: str,
    account_label: str,
    positions: tuple[str, ...],
    horizons: str,
    model_version: str,
) -> None:
    """Freeze ACTUAL + HOLD_BASELINE decisions for each SYMBOL:DATE pair as
    of that decision date, then score both against future prices.

    Point-in-time by construction: decision freezing (services/walkforward/
    decisions.py) never imports a price repository at all, so no future
    price can influence the frozen decision — only the separate outcome-
    scoring step (services/walkforward/outcomes.py), which runs strictly
    after, ever sees prices with trading_date beyond the decision date.
    """
    asyncio.run(_walk_forward_run(user_id, broker, account_label, positions, horizons, model_version))


async def _walk_forward_run(
    user_id: str | None,
    broker: str,
    account_label: str,
    positions: tuple[str, ...],
    horizons: str,
    model_version: str,
) -> None:
    from datetime import date as date_cls

    from investing_agent.db.repositories.broker_history import BrokerAccountRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.walkforward.runner import run_walk_forward

    settings = get_settings()
    resolved_user_id = user_id or settings.default_user_id

    symbol_dates: list[tuple[str, date_cls]] = []
    for raw in positions:
        symbol, _, date_str = raw.partition(":")
        if not symbol or not date_str:
            click.echo(f"  ERROR: invalid --position {raw!r}, expected SYMBOL:YYYY-MM-DD", err=True)
            sys.exit(1)
        symbol_dates.append((symbol.upper(), date_cls.fromisoformat(date_str)))
    horizons_months = tuple(int(h.strip()) for h in horizons.split(","))

    async with AsyncSessionLocal() as session:
        account_repo = BrokerAccountRepository(session)
        account = await account_repo.get_by_label(resolved_user_id, broker, account_label)
        if account is None:
            click.echo(
                f"  ERROR: no BrokerAccount for user={resolved_user_id} broker={broker} "
                f"account_label={account_label}.",
                err=True,
            )
            sys.exit(1)

        try:
            result = await run_walk_forward(
                session,
                broker_account_id=account.id,
                symbol_dates=symbol_dates,
                horizons_months=horizons_months,
                model_version=model_version,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            click.echo(f"  ERROR: {exc}", err=True)
            sys.exit(1)

    click.echo(f"\nWalk-forward run {result.run.id} — {len(result.entries)} decisions frozen+scored")
    for entry in result.entries:
        d, o = entry.decision, entry.outcome
        click.echo(
            f"  {d.symbol:12s} {d.decision_at} [{d.decision_source:13s}] action={d.action:6s} "
            f"qty={d.quantity_held}  data_quality={d.data_quality_status}  "
            f"outcome={o.outcome_status}"
        )


@cli.command("walk-forward-show")
@click.option("--run-id", required=True, help="WalkForwardRun UUID")
def walk_forward_show_cmd(run_id: str) -> None:
    """Full report for one walk-forward run: portfolio state at T, actual
    action, HOLD baseline, agent action if available, stock/NIFTY 1M/3M/6M/
    12M returns, excess return, and data-quality status for every decision."""
    asyncio.run(_walk_forward_show(run_id))


async def _walk_forward_show(run_id: str) -> None:
    import uuid as uuid_module

    from investing_agent.db.repositories.walkforward import (
        WalkForwardDecisionRepository,
        WalkForwardOutcomeRepository,
        WalkForwardRunRepository,
    )
    from investing_agent.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        run = await WalkForwardRunRepository(session).get(uuid_module.UUID(run_id))
        if run is None:
            click.echo(f"  ERROR: no WalkForwardRun with id={run_id}", err=True)
            sys.exit(1)

        decisions = await WalkForwardDecisionRepository(session).list_for_run(run.id)
        outcome_repo = WalkForwardOutcomeRepository(session)
        by_symbol_date: dict[tuple[str, object], dict[str, object]] = {}
        for d in decisions:
            key = (d.symbol, d.decision_at)
            outcome = await outcome_repo.get_by_decision_id(d.id)
            by_symbol_date.setdefault(key, {})[d.decision_source] = (d, outcome)

    click.echo(
        f"\nWalk-forward run {run.id} — broker_account={run.broker_account_id} "
        f"strategy={run.strategy_profile}  horizons={run.horizons_months}  model={run.model_version}"
    )

    for (symbol, decision_at), by_source in sorted(by_symbol_date.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        click.echo(f"\n{symbol}  decision_at={decision_at}")
        actual = by_source.get("ACTUAL")
        hold = by_source.get("HOLD_BASELINE")
        agent = by_source.get("AGENT")

        if actual:
            d, _o = actual
            click.echo(
                f"  portfolio state at T : qty={d.quantity_held}  avg_cost={d.average_cost}  "
                f"invested={d.invested_capital}"
            )
        for label, entry in (("ACTUAL", actual), ("HOLD_BASELINE", hold), ("AGENT", agent)):
            if entry is None:
                if label == "AGENT":
                    click.echo("  AGENT           : not available (no deterministic recommendation generator in v1)")
                continue
            d, o = entry
            click.echo(f"  {label:15s}: action={d.action:6s}  data_quality={d.data_quality_status}")
            if o is not None:
                click.echo(
                    f"    entry_price={o.entry_price} ({o.entry_price_date})  outcome_status={o.outcome_status}"
                )
                for h in ("1m", "3m", "6m", "12m"):
                    stock_r = getattr(o, f"stock_return_{h}")
                    bench_r = getattr(o, f"benchmark_return_{h}")
                    excess_r = getattr(o, f"excess_return_{h}")
                    tri_r = getattr(o, f"benchmark_tri_return_{h}", None)
                    tri_excess_r = getattr(o, f"excess_return_tri_{h}", None)
                    click.echo(
                        f"    {h.upper():>4s}: stock={stock_r}  nifty_price={bench_r}  "
                        f"excess_price={excess_r}  nifty_tri={tri_r}  excess_tri={tri_excess_r}"
                    )
                if o.data_quality_notes:
                    for note in o.data_quality_notes:
                        click.echo(f"    note: {note}")


@cli.command("backfill-walk-forward-tri")
@click.option("--run-id", required=True, help="WalkForwardRun UUID")
def backfill_walk_forward_tri_cmd(run_id: str) -> None:
    """Add NIFTY 50 TRI returns to existing frozen outcomes.

    This only populates the additive Phase 6G TRI columns. It never changes a
    frozen decision, price-index return, outcome status, or recommendation.
    """
    asyncio.run(_backfill_walk_forward_tri(run_id))


async def _backfill_walk_forward_tri(run_id: str) -> None:
    import uuid as uuid_module

    from investing_agent.db.repositories.walkforward import (
        WalkForwardDecisionRepository,
        WalkForwardRunRepository,
    )
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.walkforward.outcomes import score_outcome

    async with AsyncSessionLocal() as session:
        run = await WalkForwardRunRepository(session).get(uuid_module.UUID(run_id))
        if run is None:
            click.echo(f"ERROR: no WalkForwardRun with id={run_id}", err=True)
            sys.exit(1)
        decisions = await WalkForwardDecisionRepository(session).list_for_run(run.id)
        for decision in decisions:
            await score_outcome(session, decision=decision)
        await session.commit()
    click.echo(f"Backfilled NIFTY_50_TRI returns for {len(decisions)} frozen decisions in run {run_id}.")


@cli.command("walk-forward-bulk-run")
@click.option("--user-id", default=None, help="User ID (defaults to DEFAULT_USER_ID from .env)")
@click.option("--broker", required=True, type=click.Choice(["ZERODHA", "INDMONEY"]))
@click.option("--account-label", required=True)
@click.option(
    "--horizons", default="1,3,6,12", help="Comma-separated month horizons (default: 1,3,6,12)"
)
@click.option("--model-version", default="walkforward-v1")
def walk_forward_bulk_run_cmd(
    user_id: str | None, broker: str, account_label: str, horizons: str, model_version: str
) -> None:
    """Phase 6D: generate a walk-forward decision point for every real
    (symbol, trade_date) pair in this account's tradebook (BUY/ADD/REDUCE/
    EXIT, whatever genuinely happened), then freeze + score ACTUAL and
    HOLD_BASELINE for all of them via the unmodified Phase 6C pipeline --
    same PIT guarantees, no new decision logic. Run walk-forward-report
    afterward for the aggregate diagnostics."""
    asyncio.run(_walk_forward_bulk_run(user_id, broker, account_label, horizons, model_version))


async def _walk_forward_bulk_run(
    user_id: str | None, broker: str, account_label: str, horizons: str, model_version: str
) -> None:
    from investing_agent.db.repositories.broker_history import BrokerAccountRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.walkforward.runner import run_bulk_walk_forward

    settings = get_settings()
    resolved_user_id = user_id or settings.default_user_id
    horizons_months = tuple(int(h.strip()) for h in horizons.split(","))

    async with AsyncSessionLocal() as session:
        account_repo = BrokerAccountRepository(session)
        account = await account_repo.get_by_label(resolved_user_id, broker, account_label)
        if account is None:
            click.echo(
                f"  ERROR: no BrokerAccount for user={resolved_user_id} broker={broker} "
                f"account_label={account_label}.",
                err=True,
            )
            sys.exit(1)

        try:
            result = await run_bulk_walk_forward(
                session,
                broker_account_id=account.id,
                horizons_months=horizons_months,
                model_version=model_version,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            click.echo(f"  ERROR: {exc}", err=True)
            sys.exit(1)

    n_points = len(result.entries) // 2  # ACTUAL + HOLD_BASELINE per decision point
    click.echo(
        f"\nBulk walk-forward run {result.run.id} — {n_points} decision points "
        f"({len(result.entries)} decisions frozen+scored)"
    )
    action_counts: dict[str, int] = {}
    for entry in result.entries:
        if entry.decision.decision_source != "ACTUAL":
            continue
        action_counts[entry.decision.action] = action_counts.get(entry.decision.action, 0) + 1
    click.echo("  Actions: " + ", ".join(f"{k}={v}" for k, v in sorted(action_counts.items())))
    click.echo(f"  Run walk-forward-report --run-id {result.run.id} for the aggregate report.")


@cli.command("walk-forward-report")
@click.option("--run-id", required=True, help="WalkForwardRun UUID")
@click.option(
    "--csv-out", default=None, type=click.Path(dir_okay=False),
    help="Write the full event-level audit table to this CSV path (trade -> decision -> outcome traceability)",
)
def walk_forward_report_cmd(run_id: str, csv_out: str | None) -> None:
    """Aggregate diagnostics for a walk-forward run: scored/unscorable
    counts, median/mean stock and excess returns, positive-return and
    benchmark-outperformance rates, actual-vs-HOLD delta, and max-drawdown
    distribution -- broken down by action, horizon, calendar year,
    holding-age bucket, and position-concentration bucket. Every aggregate
    number traces back to an individual decision via --csv-out."""
    asyncio.run(_walk_forward_report(run_id, csv_out))


async def _walk_forward_report(run_id: str, csv_out: str | None) -> None:
    import csv as csv_module
    import uuid as uuid_module

    from investing_agent.db.repositories.walkforward import (
        WalkForwardDecisionRepository,
        WalkForwardOutcomeRepository,
        WalkForwardRunRepository,
    )
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.walkforward.aggregation import build_report
    from investing_agent.services.walkforward.audit import build_audit_rows
    from investing_agent.services.walkforward.runner import WalkForwardEntry

    async with AsyncSessionLocal() as session:
        run = await WalkForwardRunRepository(session).get(uuid_module.UUID(run_id))
        if run is None:
            click.echo(f"  ERROR: no WalkForwardRun with id={run_id}", err=True)
            sys.exit(1)

        decisions = await WalkForwardDecisionRepository(session).list_for_run(run.id)
        outcome_repo = WalkForwardOutcomeRepository(session)
        entries: list[WalkForwardEntry] = []
        for d in decisions:
            o = await outcome_repo.get_by_decision_id(d.id)
            if o is None:
                continue
            entries.append(WalkForwardEntry(decision=d, outcome=o))

        rows = await build_audit_rows(session, broker_account_id=run.broker_account_id, entries=entries)

    if not rows:
        click.echo("\nNo decisions found for this run.")
        return

    report = build_report(rows)
    click.echo(
        f"\nWalk-forward report — run {run.id}  events={report['n_events_total']}  "
        f"included={report['n_events_included']}"
    )
    click.echo("\nData quality:")
    for k, v in sorted(report["data_quality_counts"].items()):
        click.echo(f"  {k}: {v}")

    dd = report["drawdown_distribution"]
    click.echo(
        f"\nMax drawdown (n={dd['n']}): median={dd['median_pct']}%  mean={dd['mean_pct']}%  "
        f"worst={dd['worst_pct']}%  best={dd['best_pct']}%"
    )

    def _print_bucket_group(title: str, groups: dict[str, dict]) -> None:
        click.echo(f"  {title}:")
        for key, b in sorted(groups.items()):
            click.echo(
                f"    {key:10s} n={b['n_scored_this_horizon']:<4d} "
                f"median_stock={b['median_stock_return_pct']}%  median_excess={b['median_excess_return_pct']}%  "
                f"outperform_rate={b['benchmark_outperform_rate_pct']}%"
            )

    for horizon in ("1m", "3m", "6m", "12m"):
        h = report["by_horizon"][horizon]
        tri_h = report["by_horizon_tri"][horizon]
        click.echo(f"\n== {horizon.upper()} ==")
        o = h["overall"]
        click.echo(
            f"  overall n_included={o['n_included']} n_scored={o['n_scored_this_horizon']} "
            f"median_stock={o['median_stock_return_pct']}%  mean_stock={o['mean_stock_return_pct']}%  "
            f"median_excess={o['median_excess_return_pct']}%  mean_excess={o['mean_excess_return_pct']}%  "
            f"positive_rate={o['positive_return_rate_pct']}%  outperform_rate={o['benchmark_outperform_rate_pct']}%\n"
            f"  decision $ impact (actual vs HOLD, INR): median={o['decision_dollar_impact_median']}  "
            f"mean={o['decision_dollar_impact_mean']}  positive_rate={o['decision_dollar_impact_positive_rate_pct']}% "
            f"(n={o['decision_dollar_impact_n']})"
        )
        _print_bucket_group("by action", h["by_action"])
        _print_bucket_group("by year", h["by_year"])
        _print_bucket_group("by holding age", h["by_holding_age"])
        _print_bucket_group("by concentration", h["by_concentration"])
        tri_overall = tri_h["overall"]
        click.echo(
            f"  NIFTY 50 TRI comparison: n_scored={tri_overall['n_scored_this_horizon']} "
            f"median_excess={tri_overall['median_excess_return_pct']}% "
            f"outperform_rate={tri_overall['benchmark_outperform_rate_pct']}%"
        )

    if csv_out:
        with open(csv_out, "w", newline="") as f:
            writer = csv_module.writer(f)
            header = [
                "symbol", "decision_at", "decision_id", "hold_decision_id", "action",
                "data_quality_status", "outcome_status", "quantity_held", "invested_capital",
                "holding_age_days", "concentration_pct", "calendar_year", "entry_price",
            ]
            for h_label in ("1m", "3m", "6m", "12m"):
                header += [
                    f"stock_return_{h_label}", f"benchmark_return_{h_label}",
                    f"excess_return_{h_label}", f"hold_stock_return_{h_label}",
                ]
            header += ["max_drawdown_pct", "included_in_aggregate", "exclusion_reason", "data_quality_notes"]
            writer.writerow(header)
            for r in rows:
                row = [
                    r.symbol, r.decision_at, r.decision_id, r.hold_decision_id, r.action,
                    r.data_quality_status, r.outcome_status, r.quantity_held, r.invested_capital,
                    r.holding_age_days, r.concentration_pct, r.calendar_year, r.entry_price,
                ]
                for h_label in ("1m", "3m", "6m", "12m"):
                    row += [
                        r.stock_return[h_label], r.benchmark_return[h_label],
                        r.excess_return[h_label], r.hold_stock_return[h_label],
                    ]
                row += [
                    r.max_drawdown_pct, r.included_in_aggregate, r.exclusion_reason,
                    "; ".join(r.data_quality_notes),
                ]
                writer.writerow(row)
        click.echo(f"\nEvent-level audit table written to {csv_out} ({len(rows)} rows)")


@cli.command("personal-policy-report")
@click.option("--user-id", default=None, help="User ID (defaults to DEFAULT_USER_ID from .env)")
@click.option("--broker", required=True, type=click.Choice(["ZERODHA", "INDMONEY"]))
@click.option("--account-label", required=True, help="Broker account label")
@click.option("--run-id", default=None, help="Phase 6D run UUID; defaults to the newest account run")
@click.option("--benchmark", default="PRICE_INDEX", type=click.Choice(["PRICE_INDEX", "TRI"]))
@click.option("--csv-out", default=None, type=click.Path(dir_okay=False), help="Write patterns and high-impact event traces to CSV")
def personal_policy_report_cmd(
    user_id: str | None, broker: str, account_label: str, run_id: str | None, benchmark: str, csv_out: str | None
) -> None:
    """Phase 6E descriptive learning from a frozen Phase 6D run only."""
    asyncio.run(_personal_policy_report(user_id, broker, account_label, run_id, benchmark, csv_out))


async def _personal_policy_report(
    user_id: str | None,
    broker: str,
    account_label: str,
    run_id: str | None,
    benchmark: str,
    csv_out: str | None,
) -> None:
    import csv as csv_module
    import json
    import uuid as uuid_module

    from investing_agent.db.repositories.broker_history import BrokerAccountRepository
    from investing_agent.db.repositories.company import CompanyRepository
    from investing_agent.db.repositories.walkforward import (
        WalkForwardDecisionRepository,
        WalkForwardOutcomeRepository,
        WalkForwardRunRepository,
    )
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.personal_policy import (
        HORIZONS,
        build_patterns,
        derive_market_regimes,
        ranked_impacts,
    )
    from investing_agent.services.walkforward.audit import build_audit_rows
    from investing_agent.services.walkforward.runner import WalkForwardEntry

    settings = get_settings()
    resolved_user_id = user_id or settings.default_user_id
    async with AsyncSessionLocal() as session:
        account = await BrokerAccountRepository(session).get_by_label(
            resolved_user_id, broker, account_label
        )
        if account is None:
            click.echo(f"ERROR: no {broker}/{account_label} account for user={resolved_user_id}.", err=True)
            sys.exit(1)
        run_repo = WalkForwardRunRepository(session)
        run = await run_repo.get(uuid_module.UUID(run_id)) if run_id else await run_repo.latest_for_account(account.id)
        if run is None or run.broker_account_id != account.id:
            click.echo("ERROR: no matching frozen Phase 6D run for this account.", err=True)
            sys.exit(1)
        decisions = await WalkForwardDecisionRepository(session).list_for_run(run.id)
        outcome_repo = WalkForwardOutcomeRepository(session)
        entries = [
            WalkForwardEntry(decision=decision, outcome=outcome)
            for decision in decisions
            if (outcome := await outcome_repo.get_by_decision_id(decision.id)) is not None
        ]
        rows = await build_audit_rows(session, broker_account_id=account.id, entries=entries)
        company_repo = CompanyRepository(session)
        sectors = {}
        for symbol in {row.symbol for row in rows}:
            company = await company_repo.get_by_symbol(symbol)
            sectors[symbol] = company.sector if company else None
        regimes = await derive_market_regimes(session, rows)

    if not rows:
        click.echo("No Phase 6D decisions/outcomes found for this run.")
        return

    patterns = build_patterns(
        rows, sectors_by_symbol=sectors, regimes_by_decision_id=regimes, benchmark_kind=benchmark
    )
    meaningful = [pattern for pattern in patterns if pattern.stats.sample_label != "INSUFFICIENT_EVIDENCE"]
    insufficient = [pattern for pattern in patterns if pattern.stats.sample_label == "INSUFFICIENT_EVIDENCE"]
    click.echo(f"\nPersonal policy report — run={run.id}, benchmark={benchmark}, events={len(rows)}")
    click.echo("Descriptive historical learning only; recommendations and decision rules are unchanged.")

    def print_patterns(title: str, values) -> None:
        click.echo(f"\n== {title} ==")
        for pattern in values:
            stats = pattern.stats
            click.echo(
                f"{pattern.population:14s} {pattern.bucket:20s} {pattern.horizon.upper():3s} "
                f"n={stats.n:<3d} {stats.sample_label:21s} "
                f"med_excess={stats.median_excess_return_pct}% impact={stats.median_decision_dollar_impact}"
            )

    for dimension, title in (
        ("overall", "Overall / excluding 2020"),
        ("action", "By action"),
        ("calendar_year", "By year"),
        ("holding_age", "By holding age"),
        ("concentration", "By concentration"),
        ("market_regime", "By deterministic market regime"),
        ("symbol", "By symbol (n >= 5)"),
        ("sector", "By sector (n >= 5)"),
    ):
        print_patterns(title, [pattern for pattern in meaningful if pattern.dimension == dimension])
    print_patterns("Insufficient-evidence buckets", insufficient)

    impact_rows = []
    for horizon in HORIZONS:
        positive, negative = ranked_impacts(rows, horizon)
        click.echo(f"\n== {horizon.upper()} largest incremental INR impacts ==")
        for sign, events in (("positive", positive), ("negative", negative)):
            for event in events:
                click.echo(
                    f"{sign:8s} {event.symbol:12s} {event.decision_at} {event.action:6s} "
                    f"INR {event.decision_dollar_impact} decision={event.decision_id}"
                )
                impact_rows.append((sign, event))

    if csv_out:
        with open(csv_out, "w", newline="", encoding="utf-8") as output:
            writer = csv_module.DictWriter(
                output,
                fieldnames=[
                    "record_type", "benchmark", "population", "dimension", "bucket", "horizon", "n", "sample_label",
                    "median_absolute_return_pct", "median_excess_return_pct", "benchmark_outperformance_rate_pct",
                    "positive_return_rate_pct", "median_drawdown_pct", "max_drawdown_pct",
                    "median_decision_dollar_impact", "positive_dollar_impact_rate_pct", "decision_ids",
                    "impact_sign", "symbol", "decision_at", "action", "decision_dollar_impact",
                    "decision_id", "hold_decision_id", "historical_trade_evidence",
                ],
            )
            writer.writeheader()
            for pattern in patterns:
                stats = pattern.stats
                writer.writerow({
                    "record_type": "pattern", "benchmark": pattern.benchmark_kind, "population": pattern.population,
                    "dimension": pattern.dimension, "bucket": pattern.bucket, "horizon": pattern.horizon,
                    "n": stats.n, "sample_label": stats.sample_label,
                    "median_absolute_return_pct": stats.median_absolute_return_pct,
                    "median_excess_return_pct": stats.median_excess_return_pct,
                    "benchmark_outperformance_rate_pct": stats.benchmark_outperformance_rate_pct,
                    "positive_return_rate_pct": stats.positive_return_rate_pct,
                    "median_drawdown_pct": stats.median_drawdown_pct,
                    "max_drawdown_pct": stats.max_drawdown_pct,
                    "median_decision_dollar_impact": stats.median_decision_dollar_impact,
                    "positive_dollar_impact_rate_pct": stats.positive_dollar_impact_rate_pct,
                    "decision_ids": ";".join(pattern.decision_ids),
                })
            for sign, event in impact_rows:
                writer.writerow({
                    "record_type": "impact_event", "benchmark": benchmark, "horizon": event.horizon, "impact_sign": sign,
                    "symbol": event.symbol, "decision_at": event.decision_at, "action": event.action,
                    "decision_dollar_impact": event.decision_dollar_impact, "decision_id": event.decision_id,
                    "hold_decision_id": event.hold_decision_id,
                    "historical_trade_evidence": json.dumps(event.trade_evidence),
                })
        click.echo(f"\nAudit CSV written to {csv_out}")


@cli.command("candidate-policy-run")
@click.option("--user-id", default=None, help="User ID (defaults to DEFAULT_USER_ID from .env)")
@click.option("--broker", required=True, type=click.Choice(["ZERODHA", "INDMONEY"]))
@click.option("--account-label", required=True)
@click.option("--run-id", required=True, help="Frozen Phase 6D WalkForwardRun UUID")
@click.option("--benchmark", default="PRICE_INDEX", type=click.Choice(["PRICE_INDEX", "TRI"]))
def candidate_policy_run_cmd(
    user_id: str | None, broker: str, account_label: str, run_id: str, benchmark: str
) -> None:
    """Build and validate Phase 6F candidates; never enables a live rule."""
    asyncio.run(_candidate_policy_run(user_id, broker, account_label, run_id, benchmark))


async def _candidate_policy_run(
    user_id: str | None, broker: str, account_label: str, run_id: str, benchmark: str
) -> None:
    import uuid as uuid_module

    from investing_agent.db.repositories.broker_history import BrokerAccountRepository
    from investing_agent.db.repositories.policy import (
        CandidatePolicyRuleRepository,
        PolicyProposalRepository,
    )
    from investing_agent.db.repositories.walkforward import (
        WalkForwardDecisionRepository,
        WalkForwardOutcomeRepository,
        WalkForwardRunRepository,
    )
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.schemas.policy import CandidatePolicyRuleCreate, PolicyProposalCreate
    from investing_agent.services.governed_policy import (
        candidate_specs,
        metrics_as_dict,
        validate_expanding,
    )
    from investing_agent.services.walkforward.audit import build_audit_rows
    from investing_agent.services.walkforward.runner import WalkForwardEntry

    settings = get_settings()
    resolved_user_id = user_id or settings.default_user_id
    async with AsyncSessionLocal() as session:
        account = await BrokerAccountRepository(session).get_by_label(
            resolved_user_id, broker, account_label
        )
        run = await WalkForwardRunRepository(session).get(uuid_module.UUID(run_id))
        if account is None or run is None or run.broker_account_id != account.id:
            click.echo("ERROR: run does not belong to the specified broker account.", err=True)
            sys.exit(1)
        decisions = await WalkForwardDecisionRepository(session).list_for_run(run.id)
        outcome_repo = WalkForwardOutcomeRepository(session)
        entries = []
        for decision in decisions:
            outcome = await outcome_repo.get_by_decision_id(decision.id)
            if outcome is not None:
                entries.append(WalkForwardEntry(decision=decision, outcome=outcome))
        rows = await build_audit_rows(session, broker_account_id=account.id, entries=entries)
        rule_repo = CandidatePolicyRuleRepository(session)
        proposal_repo = PolicyProposalRepository(session)
        for spec in candidate_specs():
            evaluation = validate_expanding(rows, spec, benchmark)
            persisted_rule_id = f"{account.id}:{benchmark}:{spec.rule_id}"
            rule = await rule_repo.create_or_replace_draft(CandidatePolicyRuleCreate(
                broker_account_id=account.id,
                rule_id=persisted_rule_id,
                strategy_profile=run.strategy_profile,
                feature_condition=spec.condition,
                affected_action=spec.affected_action,
                proposed_adjustment=spec.adjustment,
                evidence_window={"run_id": str(run.id), "horizon": "12m", "benchmark": benchmark, "validation": "expanding_yearly"},
                sample_size=evaluation.full_history.n,
                supporting_metrics={
                    "full_history": metrics_as_dict(evaluation.full_history),
                    "excluding_2020": metrics_as_dict(evaluation.excluding_2020),
                    "folds": [
                        {"train_end_year": fold.train_end_year, "validation_year": fold.validation_year,
                         "train": metrics_as_dict(fold.train), "validation": metrics_as_dict(fold.validation)}
                        for fold in evaluation.folds
                    ],
                    "rejection_reasons": list(evaluation.rejection_reasons),
                    "complexity": spec.complexity,
                },
                confidence=evaluation.confidence,
                status=evaluation.status,
            ))
            proposal_decision = "APPROVE" if evaluation.status == "BACKTESTED" else "REJECT"
            await proposal_repo.create_or_replace(PolicyProposalCreate(
                candidate_rule_id=rule.id,
                current_behavior="Base actions remain the frozen Phase 6D ACTUAL decisions; no live policy layer exists.",
                historical_evidence=(
                    f"n={evaluation.full_history.n}; 12M median excess="
                    f"{evaluation.full_history.median_excess_return_pct}%; "
                    f"excluding 2020={evaluation.excluding_2020.median_excess_return_pct}% ({benchmark})."
                ),
                proposed_adjustment=str(spec.adjustment),
                expected_benefit=(
                    "Candidate cohort is compared with actual incremental INR impact and the zero-impact HOLD baseline; "
                    "no frozen deterministic-agent comparator is available."
                ),
                known_risks="; ".join(evaluation.rejection_reasons) or "Requires explicit human approval; never overrides hard risk controls.",
                out_of_sample_result={
                    "status": evaluation.status,
                    "folds": [
                        {"validation_year": fold.validation_year, "validation": metrics_as_dict(fold.validation)}
                        for fold in evaluation.folds
                    ],
                },
                decision=proposal_decision,
            ))
            click.echo(
                f"{spec.rule_id}: {evaluation.status} n={evaluation.full_history.n} "
                f"reasons={'; '.join(evaluation.rejection_reasons) or 'eligible for human review'}"
            )
        await session.commit()

    click.echo("\nCandidates recorded for manual review only; none are active in recommendations.")


@cli.command("valuation-multiple-record")
@click.argument("symbol")
@click.option("--pe-low", required=True, type=Decimal)
@click.option("--pe-mid", required=True, type=Decimal)
@click.option("--pe-high", required=True, type=Decimal)
@click.option("--effective-at", required=True, type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]))
@click.option("--rationale", required=True)
@click.option("--provenance", required=True, help="Auditable source/policy reference; never an analyst target.")
@click.option("--source-document-id", default=None)
def valuation_multiple_record_cmd(symbol: str, pe_low: Decimal, pe_mid: Decimal, pe_high: Decimal, effective_at: datetime, rationale: str, provenance: str, source_document_id: str | None) -> None:
    """Record an explicit immutable PE range for deterministic-valuation-v1."""
    if not (pe_low > 0 and pe_low <= pe_mid <= pe_high):
        raise click.UsageError("Require 0 < pe-low <= pe-mid <= pe-high.")
    asyncio.run(_valuation_multiple_record(symbol, pe_low, pe_mid, pe_high, effective_at, rationale, provenance, source_document_id))


async def _valuation_multiple_record(symbol: str, pe_low: Decimal, pe_mid: Decimal, pe_high: Decimal, effective_at: datetime, rationale: str, provenance: str, source_document_id: str | None) -> None:
    from investing_agent.db.models import SourceDocument, ValuationMultipleInput
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.valuation import MODEL_VERSION
    async with AsyncSessionLocal() as session:
        company = await _resolve_company(session, symbol)
        document_id = uuid.UUID(source_document_id) if source_document_id else None
        if document_id is not None:
            doc = await session.get(SourceDocument, document_id)
            if doc is None or doc.company_id != company.id:
                raise click.UsageError("source-document-id must belong to the specified company.")
        cutoff = effective_at.replace(tzinfo=UTC) if effective_at.tzinfo is None else effective_at.astimezone(UTC)
        row = ValuationMultipleInput(company_id=company.id, model_version=MODEL_VERSION, earnings_horizon="TTM", effective_at=cutoff, available_at=cutoff, pe_low=pe_low, pe_mid=pe_mid, pe_high=pe_high, rationale=rationale, provenance={"reference": provenance, "earnings_horizon": "TTM"}, source_document_id=document_id)
        session.add(row)
        await session.commit()
    click.echo(f"Recorded immutable PE range id={row.id} for {symbol.upper()} ({pe_low}/{pe_mid}/{pe_high}).")


@cli.command("valuation-run")
@click.option("--symbols", "symbol_options", multiple=True, help="Validation-only explicit symbols; accepts `--symbols BEL HAL ...`.")
@click.argument("symbols", nargs=-1)
@click.option("--as-of", required=True, type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]))
def valuation_run_cmd(symbols: tuple[str, ...], symbol_options: tuple[str, ...], as_of: datetime) -> None:
    """Run deterministic-valuation-v1 for an explicit validation cohort."""
    selected = [*symbol_options, *symbols]
    if not selected:
        raise click.UsageError("Provide at least one symbol.")
    asyncio.run(_valuation_run(selected, as_of))


async def _valuation_run(symbols: list[str], as_of: datetime) -> None:
    from investing_agent.db.repositories.company import CompanyRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.valuation import generate_for_company
    cutoff = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)
    async with AsyncSessionLocal() as session:
        company_repo = CompanyRepository(session)
        for symbol in sorted({s.upper() for s in symbols}):
            company = await company_repo.get_by_symbol(symbol)
            if company is None:
                click.echo(f"{symbol}: INSUFFICIENT_EVIDENCE company_not_resolved")
                continue
            outcome = await generate_for_company(session, company=company, as_of=cutoff)
            if outcome.snapshot is None:
                click.echo(f"{symbol}: INSUFFICIENT_EVIDENCE {', '.join(outcome.gaps)}")
            else:
                row = outcome.snapshot
                click.echo(f"{symbol}: snapshot={row.id} EPS={row.eps_low}/{row.eps_mid}/{row.eps_high} PE={row.pe_low}/{row.pe_mid}/{row.pe_high} FV={row.fair_value_low}/{row.fair_value_mid}/{row.fair_value_high}")
        await session.commit()


@cli.command("active-thesis-create")
@click.argument("symbol")
@click.option("--as-of", required=True, type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]))
@click.option("--thesis", required=True)
@click.option("--driver", "drivers", multiple=True, required=True)
@click.option("--risk", "risks", multiple=True, required=True)
@click.option("--catalyst", "catalysts", multiple=True, required=True)
@click.option("--invalidation", "invalidations", multiple=True, required=True)
@click.option("--financial-result-id", "financial_result_ids", multiple=True, required=True)
@click.option("--source-document-id", "source_document_ids", multiple=True)
@click.option("--user-id", default=None)
def active_thesis_create_cmd(symbol: str, as_of: datetime, thesis: str, drivers: tuple[str, ...], risks: tuple[str, ...], catalysts: tuple[str, ...], invalidations: tuple[str, ...], financial_result_ids: tuple[str, ...], source_document_ids: tuple[str, ...], user_id: str | None) -> None:
    """Create a PIT-safe, immutable active-thesis-v1 artifact; never trades."""
    asyncio.run(_active_thesis_create(symbol, as_of, thesis, list(drivers), list(risks), list(catalysts), list(invalidations), list(financial_result_ids), list(source_document_ids), user_id))


async def _active_thesis_create(symbol: str, as_of: datetime, thesis: str, drivers: list[str], risks: list[str], catalysts: list[str], invalidations: list[str], financial_result_ids: list[str], source_document_ids: list[str], user_id: str | None) -> None:
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.active_theses import ThesisEvidenceError, create_active_thesis
    cutoff = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)
    try:
        result_ids = [uuid.UUID(value) for value in financial_result_ids]
        document_ids = [uuid.UUID(value) for value in source_document_ids]
    except ValueError as exc:
        raise click.UsageError("financial-result-id and source-document-id must be UUIDs") from exc
    async with AsyncSessionLocal() as session:
        try:
            thesis_row = await create_active_thesis(session, user_id=user_id or get_settings().default_user_id, symbol=symbol, as_of=cutoff, thesis=thesis, drivers=drivers, risks=risks, catalysts=catalysts, invalidation_conditions=invalidations, financial_result_ids=result_ids, source_document_ids=document_ids)
            await session.commit()
        except ThesisEvidenceError as exc:
            await session.rollback()
            raise click.UsageError(str(exc)) from exc
    click.echo(f"Created immutable active thesis id={thesis_row.id} version={thesis_row.thesis_version} model={thesis_row.thesis_model_version}")


@cli.command("historical-pe-band-run")
@click.argument("symbols", nargs=-1, required=True)
@click.option("--as-of", required=True, type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]))
def historical_pe_band_run_cmd(symbols: tuple[str, ...], as_of: datetime) -> None:
    """Create PIT historical-pe-band-v1 PE evidence for explicit symbols."""
    asyncio.run(_historical_pe_band_run(list(symbols), as_of))


async def _historical_pe_band_run(symbols: list[str], as_of: datetime) -> None:
    from investing_agent.db.repositories.company import CompanyRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.historical_pe_band import generate_for_company
    cutoff = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)
    async with AsyncSessionLocal() as session:
        repo = CompanyRepository(session)
        for symbol in sorted({symbol.upper() for symbol in symbols}):
            company = await repo.get_by_symbol(symbol)
            if company is None:
                click.echo(f"{symbol}: INSUFFICIENT_EVIDENCE company_not_resolved")
                continue
            outcome = await generate_for_company(session, company=company, as_of=cutoff)
            if outcome.multiple_input is None:
                click.echo(f"{symbol}: INSUFFICIENT_EVIDENCE valid_observations={outcome.observation_count}; excluded={len(outcome.excluded)}")
            else:
                row = outcome.multiple_input
                click.echo(f"{symbol}: multiple_input={row.id} observations={outcome.observation_count} PE={row.pe_low}/{row.pe_mid}/{row.pe_high}")
        await session.commit()


@cli.command("recommendation-run")
@click.option("--symbols", "symbol_options", multiple=True, help="Validation-only explicit symbols; accepts `--symbols BEL HAL ...`.")
@click.argument("symbols", nargs=-1)
@click.option("--as-of", required=True, type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]))
@click.option("--user-id", default=None)
def recommendation_run_cmd(symbols: tuple[str, ...], symbol_options: tuple[str, ...], as_of: datetime, user_id: str | None) -> None:
    """Run unchanged current-recommendation-v1 for an explicit validation cohort."""
    selected = [*symbol_options, *symbols]
    if not selected:
        raise click.UsageError("Provide at least one symbol.")
    asyncio.run(_recommendation_run(selected, as_of, user_id))


async def _recommendation_run(symbols: list[str], as_of: datetime, user_id: str | None) -> None:
    from sqlalchemy import select
    from investing_agent.db.models import RecommendationDecision
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.current_recommendations import generate
    cutoff = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)
    async with AsyncSessionLocal() as session:
        run = await generate(session, user_id=user_id or get_settings().default_user_id, as_of=cutoff, symbols=symbols)
        await session.commit()
        rows = list((await session.execute(select(RecommendationDecision).where(RecommendationDecision.run_id == run.id).order_by(RecommendationDecision.symbol))).scalars())
        for row in rows:
            click.echo(f"{row.symbol}: {row.final_action} confidence={row.confidence} gaps={','.join(row.data_gaps)}")
    click.echo(f"Phase 7A validation run {run.id} rule_version={run.rule_version} policy_layer=DISABLED")


@cli.command("current-recommendations-run")
@click.option("--user-id", default=None)
@click.option("--as-of", default=None, type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]))
def current_recommendations_run_cmd(user_id: str | None, as_of: datetime | None) -> None:
    """Generate Phase 7A deterministic recommendations; never executes trades."""
    asyncio.run(_current_recommendations_run(user_id, as_of))


async def _current_recommendations_run(user_id: str | None, as_of: datetime | None) -> None:
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.current_recommendations import generate
    resolved = user_id or get_settings().default_user_id
    cutoff = as_of.replace(tzinfo=UTC) if as_of else None
    async with AsyncSessionLocal() as session:
        run = await generate(session, user_id=resolved, as_of=cutoff)
        await session.commit()
    click.echo(f"Phase 7A run {run.id} rule_version={run.rule_version} policy_layer=DISABLED")


@cli.command("account-recommendation-run")
@click.option("--broker", required=True, type=click.Choice(["ZERODHA"]))
@click.option("--account-label", required=True)
@click.option("--as-of", required=True, type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]))
@click.option("--validate-symbol", "validation_symbols", multiple=True, help="Optional explicit validation-only symbols; not a watchlist change.")
@click.option("--user-id", default=None)
def account_recommendation_run_cmd(broker: str, account_label: str, as_of: datetime, validation_symbols: tuple[str, ...], user_id: str | None) -> None:
    """Run frozen recommendation-v1 using reconstructed current account context."""
    asyncio.run(_account_recommendation_run(broker, account_label, as_of, list(validation_symbols), user_id))


async def _account_recommendation_run(broker: str, account_label: str, as_of: datetime, validation_symbols: list[str], user_id: str | None) -> None:
    from sqlalchemy import select
    from investing_agent.db.models import RecommendationDecision
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.current_recommendations import generate
    from investing_agent.services.recommendation_context import resolve_account_context
    cutoff = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)
    resolved_user_id = user_id or get_settings().default_user_id
    async with AsyncSessionLocal() as session:
        context = await resolve_account_context(session, user_id=resolved_user_id, broker=broker, account_label=account_label, as_of_date=cutoff.date())
        universe = sorted(set(context.symbols) | {symbol.upper() for symbol in validation_symbols})
        run = await generate(session, user_id=resolved_user_id, as_of=cutoff, symbols=universe, account_context=context)
        await session.commit()
        rows = list((await session.execute(select(RecommendationDecision).where(RecommendationDecision.run_id == run.id).order_by(RecommendationDecision.symbol))).scalars())
        click.echo(f"Account {broker}/{account_label} strategy={context.strategy_profile} holdings={len(context.holdings)} watchlist={len(context.watchlist_symbols)} market_value_complete={context.market_value_complete} cash_partial={context.cash_balance_partial} ({context.cash_balance_caveat})")
        for row in rows:
            p = row.portfolio_context or {}
            v = row.valuation_inputs or {}
            click.echo(f"{row.symbol}: provenance={p.get('provenance')} quantity={p.get('quantity')} avg_cost={p.get('average_cost')} market_price={p.get('market_price')} weight={p.get('concentration_pct')} thesis={'yes' if 'active_thesis_missing' not in row.data_gaps else 'no'} fair_value_mid={v.get('fair_value_mid')} action={row.final_action} confidence={row.confidence} gaps={','.join(row.data_gaps)}")
    click.echo(f"Recommendation run {run.id} rule_version={run.rule_version} policy_layer=DISABLED")


@cli.command("recommendation-review")
@click.option("--decision-id", required=True, help="Frozen RecommendationDecision UUID to review.")
@click.option("--verdict", required=True, type=click.Choice(["AGREE", "DISAGREE", "UNSURE"], case_sensitive=False))
@click.option("--reason", default=None, help="Optional human review rationale; never changes the decision.")
def recommendation_review_cmd(decision_id: str, verdict: str, reason: str | None) -> None:
    """Append an immutable human observation to a frozen recommendation.

    This command records only a supplied human verdict.  It does not alter a
    recommendation, confidence, valuation, thesis, policy, or live behavior.
    """
    asyncio.run(_recommendation_review(decision_id, verdict.upper(), reason))


async def _recommendation_review(decision_id: str, verdict: str, reason: str | None) -> None:
    import uuid
    from sqlalchemy import select
    from investing_agent.db.models import RecommendationDecision, RecommendationReview
    from investing_agent.db.session import AsyncSessionLocal

    try:
        parsed_id = uuid.UUID(decision_id)
    except ValueError as exc:
        raise click.UsageError("--decision-id must be a UUID") from exc

    async with AsyncSessionLocal() as session:
        decision = (await session.execute(
            select(RecommendationDecision).where(RecommendationDecision.id == parsed_id)
        )).scalar_one_or_none()
        if decision is None:
            raise click.UsageError(f"RecommendationDecision not found: {decision_id}")
        review = RecommendationReview(
            recommendation_decision_id=decision.id,
            verdict=verdict,
            reason=reason,
        )
        session.add(review)
        await session.commit()
    click.echo(
        f"Recorded immutable review {review.id} for {decision.symbol} "
        f"verdict={review.verdict}; recommendation remains unchanged."
    )


@cli.command("recommendation-review-report")
@click.option("--run-id", default=None, help="Optional RecommendationRun UUID filter.")
@click.option("--symbol", default=None, help="Optional symbol filter.")
def recommendation_review_report_cmd(run_id: str | None, symbol: str | None) -> None:
    """Report immutable human review observations; never changes decisions."""
    asyncio.run(_recommendation_review_report(run_id, symbol.upper() if symbol else None))


async def _recommendation_review_report(run_id: str | None, symbol: str | None) -> None:
    import uuid
    from sqlalchemy import select
    from investing_agent.db.models import RecommendationDecision, RecommendationReview, RecommendationRun
    from investing_agent.db.session import AsyncSessionLocal

    parsed_run_id = None
    if run_id:
        try:
            parsed_run_id = uuid.UUID(run_id)
        except ValueError as exc:
            raise click.UsageError("--run-id must be a UUID") from exc

    async with AsyncSessionLocal() as session:
        stmt = (
            select(RecommendationReview, RecommendationDecision, RecommendationRun)
            .join(RecommendationDecision, RecommendationReview.recommendation_decision_id == RecommendationDecision.id)
            .join(RecommendationRun, RecommendationDecision.run_id == RecommendationRun.id)
            .order_by(RecommendationReview.reviewed_at.desc(), RecommendationReview.id.desc())
        )
        if parsed_run_id:
            stmt = stmt.where(RecommendationDecision.run_id == parsed_run_id)
        if symbol:
            stmt = stmt.where(RecommendationDecision.symbol == symbol)
        rows = (await session.execute(stmt)).all()

    if not rows:
        click.echo("No recommendation reviews recorded.")
        return
    for review, decision, run in rows:
        click.echo(
            f"{review.id} decision={decision.id} run={run.id} symbol={decision.symbol} "
            f"action={decision.final_action} verdict={review.verdict} "
            f"reviewed_at={review.reviewed_at.isoformat()} reason={review.reason or ''}"
        )


@cli.command("historical-agent-comparator-run")
@click.option("--run-id", required=True, help="Existing frozen Phase 6D WalkForwardRun UUID")
def historical_agent_comparator_run_cmd(run_id: str) -> None:
    """Replay deterministic-baseline-v1 offline at existing Phase 6D points."""
    asyncio.run(_historical_agent_comparator_run(run_id))


async def _historical_agent_comparator_run(run_id: str) -> None:
    import uuid as uuid_module

    from investing_agent.db.repositories.walkforward import WalkForwardRunRepository
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.walkforward.runner import replay_deterministic_baseline

    async with AsyncSessionLocal() as session:
        run = await WalkForwardRunRepository(session).get(uuid_module.UUID(run_id))
        if run is None:
            click.echo(f"ERROR: no WalkForwardRun with id={run_id}", err=True)
            sys.exit(1)
        result = await replay_deterministic_baseline(session, run=run)
        await session.commit()
    click.echo(
        f"Replayed deterministic-baseline-v1 for {len(result.entries)} historical points; "
        "offline only, no live recommendations changed."
    )


@cli.command("historical-agent-comparator-report")
@click.option("--run-id", required=True, help="Phase 6D run with AGENT comparator rows")
def historical_agent_comparator_report_cmd(run_id: str) -> None:
    """Compare deterministic-baseline-v1 with actual decisions and HOLD."""
    asyncio.run(_historical_agent_comparator_report(run_id))


async def _historical_agent_comparator_report(run_id: str) -> None:
    import uuid as uuid_module

    from investing_agent.db.repositories.walkforward import (
        WalkForwardDecisionRepository,
        WalkForwardOutcomeRepository,
        WalkForwardRunRepository,
    )
    from investing_agent.db.session import AsyncSessionLocal
    from investing_agent.services.walkforward.comparator import build_comparator_report
    from investing_agent.services.walkforward.runner import WalkForwardEntry

    async with AsyncSessionLocal() as session:
        run = await WalkForwardRunRepository(session).get(uuid_module.UUID(run_id))
        if run is None:
            click.echo(f"ERROR: no WalkForwardRun with id={run_id}", err=True)
            sys.exit(1)
        decisions = await WalkForwardDecisionRepository(session).list_for_run(run.id)
        outcome_repo = WalkForwardOutcomeRepository(session)
        entries = [
            WalkForwardEntry(decision=d, outcome=o)
            for d in decisions
            if (o := await outcome_repo.get_by_decision_id(d.id)) is not None
        ]
    report = build_comparator_report(entries)
    click.echo("\nHistorical deterministic-agent comparator — deterministic-baseline-v1 (offline only)")
    click.echo(f"events={report['events']} policy_scoreable={report['policy_scoreable']} policy_unscoreable={report['policy_unscoreable']}")
    click.echo("action distribution: " + ", ".join(f"{k}={v}" for k, v in report["action_distribution"].items()))
    for horizon, values in report["by_horizon"].items():
        click.echo(
            f"{horizon.upper()}: outcome scoreable/unscoreable={values['outcome_scoreable']}/{values['outcome_unscoreable']} "
            f"eligible={values['baseline_eligible']} median_return={values['median_stock_return_pct']}% "
            f"median_excess_TRI={values['median_excess_tri_pct']}% median_drawdown={values['median_drawdown_pct']}% "
            f"agent_vs_HOLD_INR={values['median_agent_vs_hold_impact_inr']} actual_vs_HOLD_INR={values['median_actual_vs_hold_impact_inr']}"
        )


@cli.command("policy-proposal-report")
@click.option("--user-id", default=None, help="User ID (defaults to DEFAULT_USER_ID from .env)")
@click.option("--broker", required=True, type=click.Choice(["ZERODHA", "INDMONEY"]))
@click.option("--account-label", required=True)
def policy_proposal_report_cmd(user_id: str | None, broker: str, account_label: str) -> None:
    """Print candidate-rule evidence and human review recommendations."""
    asyncio.run(_policy_proposal_report(user_id, broker, account_label))


async def _policy_proposal_report(user_id: str | None, broker: str, account_label: str) -> None:
    from investing_agent.db.repositories.broker_history import BrokerAccountRepository
    from investing_agent.db.repositories.policy import (
        CandidatePolicyRuleRepository,
        PolicyProposalRepository,
    )
    from investing_agent.db.session import AsyncSessionLocal

    settings = get_settings()
    resolved_user_id = user_id or settings.default_user_id
    async with AsyncSessionLocal() as session:
        account = await BrokerAccountRepository(session).get_by_label(
            resolved_user_id, broker, account_label
        )
        if account is None:
            click.echo(f"ERROR: no {broker}/{account_label} account for user={resolved_user_id}.", err=True)
            sys.exit(1)
        rules = await CandidatePolicyRuleRepository(session).list_for_account(account.id)
        proposal_repo = PolicyProposalRepository(session)
        proposals = [(rule, await proposal_repo.get_by_candidate_rule_id(rule.id)) for rule in rules]

    if not proposals:
        click.echo("No Phase 6F candidates. Run candidate-policy-run first.")
        return
    click.echo("\nGoverned personal policy proposals — review only; no rule is live-enabled.")
    for rule, proposal in proposals:
        click.echo(f"\n== {rule.rule_id} [{rule.status}] ==")
        click.echo(f"Condition: {rule.feature_condition}")
        click.echo(f"Adjustment: {rule.proposed_adjustment}")
        click.echo(f"n={rule.sample_size} confidence={rule.confidence}")
        if proposal is not None:
            click.echo(f"Current behavior: {proposal.current_behavior}")
            click.echo(f"Historical evidence: {proposal.historical_evidence}")
            click.echo(f"Expected benefit: {proposal.expected_benefit}")
            click.echo(f"Known risks: {proposal.known_risks}")
            click.echo(f"Out-of-sample: {proposal.out_of_sample_result}")
            click.echo(f"Review decision: {proposal.decision}")


@cli.command("policy-rule-review")
@click.argument("rule_id")
@click.option("--approve/--reject", default=False, help="Explicit human review decision; does not enable the rule")
def policy_rule_review_cmd(rule_id: str, approve: bool) -> None:
    """Record human approval/rejection without integrating any live policy."""
    asyncio.run(_policy_rule_review(rule_id, approve))


async def _policy_rule_review(rule_id: str, approve: bool) -> None:
    from investing_agent.db.repositories.policy import CandidatePolicyRuleRepository
    from investing_agent.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        repo = CandidatePolicyRuleRepository(session)
        rule = await repo.get_by_rule_id(rule_id)
        if rule is None:
            click.echo(f"ERROR: candidate rule {rule_id!r} not found.", err=True)
            sys.exit(1)
        # A rejected backtest may never be approved without a new backtest.
        if approve and rule.status == "REJECTED":
            click.echo("ERROR: rejected candidates require a new validated backtest before approval.", err=True)
            sys.exit(1)
        await repo.set_status(rule, "APPROVED" if approve else "REJECTED")
        await session.commit()
    click.echo(f"{rule_id}: {'APPROVED' if approve else 'REJECTED'} (still not live-enabled)")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
