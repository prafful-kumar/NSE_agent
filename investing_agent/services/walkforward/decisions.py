from __future__ import annotations

"""Decision freezing for Phase 6C walk-forward simulation.

freeze_decision is the structural core of the "no future price leakage"
guarantee: it only ever calls get_portfolio_as_of(..., feature_cutoff_at=T)
and HistoricalTradeRepository.list_up_to(..., T) (both already <=T-filtered)
plus company resolution. It never imports DailyPriceRepository or
BenchmarkPriceRepository -- that import simply does not exist in this
module, so a future price observation cannot leak into a frozen decision no
matter what the caller does. services/walkforward/prices.py and outcomes.py
are the only places in this feature that touch price repositories, and they
are only ever invoked after a decision is already frozen and persisted.

Three decision sources (schemas/walkforward.py's DecisionSource), never
conflated:
- ACTUAL: what the investor really did, i.e. the real post-trade portfolio
  state at decision_at, with the action classified from that day's trades.
- HOLD_BASELINE: the counterfactual "what if nothing changed" -- action is
  always HOLD by construction, and the frozen position is the portfolio
  state from the day *before* decision_at (i.e. as if decision_at's real
  trades, if any, never happened).
- AGENT: a deterministic recommendation, only ever written once a generator
  exists. No generator exists yet (see runner.py), so v1 never calls
  freeze_decision with decision_source="AGENT" -- the schema/model support
  it without inventing behavior now.
"""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import HistoricalTrade, WalkForwardDecision, WalkForwardRun
from investing_agent.db.repositories.broker_history import HistoricalTradeRepository
from investing_agent.db.repositories.walkforward import WalkForwardDecisionRepository
from investing_agent.schemas.reconstruction import PortfolioSnapshotAsOf, PositionAsOf
from investing_agent.schemas.walkforward import Action, DataQualityStatus, WalkForwardDecisionCreate
from investing_agent.services.reconstruction.service import get_portfolio_as_of

_ZERO = Decimal("0")


def classify_actual_action(
    prior_quantity: Decimal, post_quantity: Decimal, trades_on_date: list[HistoricalTrade]
) -> Action:
    """BUY if there was no position before and there is one now, ADD if the
    position grew, REDUCE if it shrank but wasn't closed, EXIT if it was
    closed, HOLD if nothing happened on decision_at (no trades that exact
    date, or a trade that happened not to change quantity)."""
    if not trades_on_date:
        return "HOLD"
    if prior_quantity == 0 and post_quantity > 0:
        return "BUY"
    if post_quantity == 0 and prior_quantity > 0:
        return "EXIT"
    if post_quantity > prior_quantity:
        return "ADD"
    if post_quantity < prior_quantity:
        return "REDUCE"
    return "HOLD"


def _find_position(snapshot: PortfolioSnapshotAsOf, symbol: str) -> PositionAsOf | None:
    for position in snapshot.positions:
        if position.symbol == symbol:
            return position
    return None


def _symbol_data_quality(
    snapshot: PortfolioSnapshotAsOf, symbol: str
) -> tuple[DataQualityStatus, list[str]]:
    """Reconstruction warnings are free-text and not symbol-tagged in a
    structured way, so relevance is judged by substring match on the
    symbol -- an unrelated symbol's warning in the same account/run must
    never taint this decision's data_quality_status."""
    matching = [w for w in snapshot.warnings if symbol in w]
    status: DataQualityStatus = "RECONSTRUCTION_WARNING" if matching else "CLEAN"
    return status, matching


async def freeze_decision(
    session: AsyncSession,
    *,
    run: WalkForwardRun,
    broker_account_id: uuid.UUID,
    symbol: str,
    decision_at: date,
    decision_source: Literal["ACTUAL", "HOLD_BASELINE"],
) -> WalkForwardDecision:
    feature_cutoff_at = datetime.combine(decision_at, datetime.max.time())
    prior_date = decision_at - timedelta(days=1)

    snapshot_at = await get_portfolio_as_of(
        session,
        broker_account_id=broker_account_id,
        as_of_date=decision_at,
        feature_cutoff_at=feature_cutoff_at,
    )
    snapshot_prior = await get_portfolio_as_of(
        session,
        broker_account_id=broker_account_id,
        as_of_date=prior_date,
        feature_cutoff_at=feature_cutoff_at,
    )

    position_at = _find_position(snapshot_at, symbol)
    position_prior = _find_position(snapshot_prior, symbol)
    post_quantity = position_at.quantity_held if position_at else _ZERO
    prior_quantity = position_prior.quantity_held if position_prior else _ZERO

    trade_repo = HistoricalTradeRepository(session)
    all_trades_at = await trade_repo.list_up_to(broker_account_id, decision_at)
    trades_on_date = [
        t for t in all_trades_at if t.trade_date == decision_at and t.symbol == symbol
    ]

    if decision_source == "ACTUAL":
        action = classify_actual_action(prior_quantity, post_quantity, trades_on_date)
        position = position_at
        evidence = [{"type": "historical_trade", "id": str(t.id)} for t in trades_on_date]
        data_quality_status, warnings = _symbol_data_quality(snapshot_at, symbol)
    else:  # HOLD_BASELINE
        action = "HOLD"
        position = position_prior
        evidence = []
        data_quality_status, warnings = _symbol_data_quality(snapshot_prior, symbol)

    quantity_held = position.quantity_held if position else _ZERO
    average_cost = position.average_cost if position else _ZERO
    invested_capital = position.invested_capital if position else _ZERO
    company_id = position.company_id if position else None

    repo = WalkForwardDecisionRepository(session)
    row, _created = await repo.create_if_not_exists(
        WalkForwardDecisionCreate(
            run_id=run.id,
            broker_account_id=broker_account_id,
            company_id=company_id,
            symbol=symbol,
            decision_at=decision_at,
            feature_cutoff_at=feature_cutoff_at,
            decision_source=decision_source,
            action=action,
            confidence=None,
            reasoning=None,
            evidence=evidence,
            quantity_held=quantity_held,
            average_cost=average_cost,
            invested_capital=invested_capital,
            reconstruction_warnings=warnings,
            data_quality_status=data_quality_status,
        )
    )
    return row


DETERMINISTIC_BASELINE_VERSION = "deterministic-baseline-v1"


async def freeze_deterministic_baseline_decision(
    session: AsyncSession,
    *,
    run: WalkForwardRun,
    broker_account_id: uuid.UUID,
    symbol: str,
    decision_at: date,
) -> WalkForwardDecision:
    """Freeze the deliberately conservative Phase 6H offline baseline.

    This is not connected to the live graph.  It uses only the reconstructed
    state immediately before T: a clean existing position maps to HOLD;
    missing/ambiguous inputs map to INSUFFICIENT_EVIDENCE.  It deliberately
    has no initiation, reduction, or exit heuristic because no such
    deterministic live policy existed historically and inventing one from
    Phase 6D outcomes would be hindsight tuning.
    """
    feature_cutoff_at = datetime.combine(decision_at, datetime.max.time())
    prior_date = decision_at - timedelta(days=1)
    snapshot = await get_portfolio_as_of(
        session,
        broker_account_id=broker_account_id,
        as_of_date=prior_date,
        feature_cutoff_at=feature_cutoff_at,
    )
    position = _find_position(snapshot, symbol)
    quality, warnings = _symbol_data_quality(snapshot, symbol)
    reason: str
    if quality != "CLEAN":
        action: Action = "INSUFFICIENT_EVIDENCE"
        reason = "reconstructed pre-decision position has unresolved warnings"
    elif position is None or position.quantity_held <= _ZERO:
        action = "INSUFFICIENT_EVIDENCE"
        reason = "no clean pre-decision position; baseline-v1 does not initiate positions"
    elif position.corporate_action_flag:
        action = "INSUFFICIENT_EVIDENCE"
        reason = "pre-decision position has a corporate-action reconstruction flag"
    else:
        action = "HOLD"
        reason = "clean pre-decision position; baseline-v1 preserves it without an unvalidated action rule"

    quantity = position.quantity_held if position else _ZERO
    average_cost = position.average_cost if position else _ZERO
    invested = position.invested_capital if position else _ZERO
    repo = WalkForwardDecisionRepository(session)
    row, _ = await repo.create_if_not_exists(
        WalkForwardDecisionCreate(
            run_id=run.id,
            broker_account_id=broker_account_id,
            company_id=position.company_id if position else None,
            symbol=symbol,
            decision_at=decision_at,
            feature_cutoff_at=feature_cutoff_at,
            decision_source="AGENT",
            action=action,
            confidence=None,
            reasoning=f"{DETERMINISTIC_BASELINE_VERSION}: {reason}.",
            evidence=[{
                "type": "deterministic_baseline",
                "version": DETERMINISTIC_BASELINE_VERSION,
                "as_of_date": str(prior_date),
                "prior_quantity": str(quantity),
                "reason": reason,
            }],
            quantity_held=quantity,
            average_cost=average_cost,
            invested_capital=invested,
            reconstruction_warnings=warnings,
            data_quality_status=quality,
        )
    )
    return row
