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
from datetime import datetime

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


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
