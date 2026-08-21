from __future__ import annotations

"""DB orchestration for portfolio reconstruction (Phase 6B).

get_portfolio_as_of is a pure read: it replays raw trade/cash-flow facts
in memory every call (823 trades today -- cheap; no need for incremental or
cached lot state in a single-user personal system). It never touches
historical_position_lots.

reconstruct_and_persist is the only writer: a full-history FIFO replay
(as_of = latest known trade date) that wholesale-replaces every
historical_position_lots row for the account, per that table's own
docstring contract. historical_position_lots exists purely as a
materialized cache of the latest reconstruction for downstream consumers
that don't want to replay raw facts themselves -- it is never the source of
truth for an arbitrary as-of-date query.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.config.logging import get_logger
from investing_agent.db.repositories.broker_history import (
    BrokerAccountRepository,
    HistoricalCashFlowRepository,
    HistoricalPositionLotRepository,
    HistoricalTradeRepository,
)
from investing_agent.schemas.broker_history import HistoricalPositionLotCreate
from investing_agent.schemas.reconstruction import (
    AppliedCorporateActionAdjustment,
    PortfolioSnapshotAsOf,
    PositionAsOf,
    ReconstructionRunResult,
)
from investing_agent.services.reconstruction.corporate_actions import (
    load_corporate_action_events,
    load_opening_adjustments,
)
from investing_agent.services.reconstruction.fifo import FifoReplayResult
from investing_agent.services.reconstruction.price_provider import HistoricalPriceProvider
from investing_agent.services.reconstruction.replay import compute_cash_breakdown, replay_trades

logger = get_logger(__name__)


def _build_snapshot(
    *,
    broker_account_id: uuid.UUID,
    strategy_profile: str,
    as_of_date: date,
    replay: FifoReplayResult,
    cash: "object",
    price_provider: HistoricalPriceProvider | None,
) -> PortfolioSnapshotAsOf:
    symbols_touched_by_synthetic_lot = {
        lot.symbol for lot in replay.all_lots if lot.is_synthetic
    }

    positions: list[PositionAsOf] = []
    for symbol in sorted(replay.symbols_with_open_lots()):
        open_lots = replay.open_lots(symbol)
        quantity_held = sum((lot.quantity_remaining for lot in open_lots), Decimal("0"))
        invested_capital = sum(
            (lot.quantity_remaining * lot.cost_price for lot in open_lots), Decimal("0")
        )
        average_cost = invested_capital / quantity_held if quantity_held else Decimal("0")

        isin = None
        company_id = None
        for lot in open_lots:
            isin = isin or lot.isin
            company_id = company_id or lot.company_id

        price_at_date: Decimal | None = None
        if price_provider is not None:
            price_at_date = price_provider.get_close(symbol, isin, as_of_date)

        unrealized_pnl = (
            quantity_held * (price_at_date - average_cost) if price_at_date is not None else None
        )

        positions.append(
            PositionAsOf(
                symbol=symbol,
                isin=isin,
                company_id=company_id,
                quantity_held=quantity_held,
                average_cost=average_cost,
                invested_capital=invested_capital,
                price_at_date=price_at_date,
                price_source="STATEMENT_DERIVED" if price_at_date is not None else "UNAVAILABLE",
                unrealized_pnl=unrealized_pnl,
                realized_pnl_cumulative=replay.realized_pnl_by_symbol.get(symbol, Decimal("0")),
                open_lot_count=len(open_lots),
                corporate_action_flag=symbol in symbols_touched_by_synthetic_lot,
            )
        )

    invested_capital_total = sum((p.invested_capital for p in positions), Decimal("0"))
    unrealized_values = [p.unrealized_pnl for p in positions]
    unrealized_pnl_total = (
        sum((v for v in unrealized_values if v is not None), Decimal("0"))
        if all(v is not None for v in unrealized_values) and positions
        else None
    )

    return PortfolioSnapshotAsOf(
        broker_account_id=broker_account_id,
        as_of_date=as_of_date,
        strategy_profile=strategy_profile,
        positions=positions,
        cash_balance_partial=cash.cash_balance_partial,
        cash_balance_caveat=cash.caveat,
        cash_balance_ledger_coverage_start=cash.cash_ledger_coverage_start,
        invested_capital_total=invested_capital_total,
        unrealized_pnl_total=unrealized_pnl_total,
        realized_pnl_cumulative_total=replay.realized_pnl_total(),
        warnings=list(replay.warnings),
        corporate_action_adjustments=[
            AppliedCorporateActionAdjustment(
                symbol=a.symbol,
                event_type=a.event_type,
                event_date=a.event_date,
                ratio_new=a.ratio_new,
                ratio_old=a.ratio_old,
                old_quantity=a.old_quantity,
                new_quantity=a.new_quantity,
                old_cost_per_share=a.old_cost_per_share,
                adjusted_cost_per_share=a.adjusted_cost_per_share,
                source=a.source,
            )
            for a in replay.corporate_action_adjustments
        ],
    )


async def get_portfolio_as_of(
    session: AsyncSession,
    *,
    broker_account_id: uuid.UUID,
    as_of_date: date,
    price_provider: HistoricalPriceProvider | None = None,
    feature_cutoff_at: datetime | None = None,
) -> PortfolioSnapshotAsOf:
    """feature_cutoff_at=None (default) preserves exact current behavior
    for every existing Phase 6B caller (sees the current latest version of
    every corporate action). Phase 6C's decision-freeze path always passes
    it explicitly, restricting corporate-action versions to those whose
    available_at <= feature_cutoff_at so a later correction/backfill can
    never leak into a decision frozen at an earlier point in time."""
    account_repo = BrokerAccountRepository(session)
    trade_repo = HistoricalTradeRepository(session)
    cash_repo = HistoricalCashFlowRepository(session)

    account = await account_repo.get(broker_account_id)
    if account is None:
        raise ValueError(f"No BrokerAccount with id={broker_account_id}")

    trades = await trade_repo.list_up_to(broker_account_id, as_of_date)
    cash_flows = await cash_repo.list_up_to(broker_account_id, as_of_date)
    ledger_coverage_start = await cash_repo.earliest_date(broker_account_id)
    corporate_actions = await load_corporate_action_events(session, as_of=feature_cutoff_at)
    opening_adjustments = await load_opening_adjustments(session, broker_account_id)

    replay = replay_trades(
        trades, corporate_actions=corporate_actions, opening_adjustments=opening_adjustments
    )
    cash = compute_cash_breakdown(trades, cash_flows, ledger_coverage_start)

    return _build_snapshot(
        broker_account_id=broker_account_id,
        strategy_profile=account.strategy_profile,
        as_of_date=as_of_date,
        replay=replay,
        cash=cash,
        price_provider=price_provider,
    )


async def reconstruct_and_persist(
    session: AsyncSession,
    *,
    broker_account_id: uuid.UUID,
    price_provider: HistoricalPriceProvider | None = None,
) -> ReconstructionRunResult:
    trade_repo = HistoricalTradeRepository(session)
    lot_repo = HistoricalPositionLotRepository(session)

    latest_date = await trade_repo.latest_date(broker_account_id)
    if latest_date is None:
        raise ValueError(f"No trades found for broker_account_id={broker_account_id}")

    snapshot = await get_portfolio_as_of(
        session,
        broker_account_id=broker_account_id,
        as_of_date=latest_date,
        price_provider=price_provider,
    )

    trades = await trade_repo.list_up_to(broker_account_id, latest_date)
    corporate_actions = await load_corporate_action_events(session)
    opening_adjustments = await load_opening_adjustments(session, broker_account_id)
    replay = replay_trades(
        trades, corporate_actions=corporate_actions, opening_adjustments=opening_adjustments
    )

    lot_creates = [
        HistoricalPositionLotCreate(
            broker_account_id=broker_account_id,
            symbol=lot.symbol,
            company_id=lot.company_id,
            opened_date=lot.opened_date,
            quantity_opened=lot.quantity_opened,
            quantity_remaining=lot.quantity_remaining,
            cost_price=lot.cost_price,
            closed_date=lot.closed_date,
            realized_pnl=lot.realized_pnl,
        )
        for lot in replay.all_lots
    ]
    written = await lot_repo.replace_all_for_account(broker_account_id, lot_creates)

    logger.info(
        "position_lots_rebuilt",
        broker_account_id=str(broker_account_id),
        as_of_date=str(latest_date),
        lots_written=len(written),
        symbols_with_open_positions=len(snapshot.positions),
        warnings=len(replay.warnings),
    )

    return ReconstructionRunResult(
        broker_account_id=broker_account_id,
        as_of_date=latest_date,
        lots_written=len(written),
        symbols_with_open_positions=len(snapshot.positions),
        warnings=replay.warnings,
        snapshot=snapshot,
    )
