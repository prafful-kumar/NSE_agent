from __future__ import annotations

"""run_walk_forward: orchestrates one batch of Phase 6C walk-forward
decisions/outcomes for a broker account.

For each requested (symbol, decision_at) pair, freezes both the ACTUAL
decision (what the investor really did) and the HOLD_BASELINE decision (the
counterfactual "do nothing"), then immediately scores both outcomes. No
AGENT decisions are produced in v1 -- no deterministic recommendation
generator exists yet (see decisions.py's module docstring).
"""

import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import WalkForwardDecision, WalkForwardOutcome, WalkForwardRun
from investing_agent.db.repositories.broker_history import BrokerAccountRepository
from investing_agent.db.repositories.walkforward import WalkForwardRunRepository
from investing_agent.schemas.walkforward import WalkForwardRunCreate
from investing_agent.services.walkforward.bulk import generate_decision_points
from investing_agent.services.walkforward.decisions import freeze_decision, freeze_deterministic_baseline_decision
from investing_agent.services.walkforward.outcomes import score_outcome

DEFAULT_HORIZONS_MONTHS = (1, 3, 6, 12)
DEFAULT_MODEL_VERSION = "walkforward-v1"


@dataclass
class WalkForwardEntry:
    decision: WalkForwardDecision
    outcome: WalkForwardOutcome


@dataclass
class WalkForwardRunResult:
    run: WalkForwardRun
    entries: list[WalkForwardEntry] = field(default_factory=list)


async def run_walk_forward(
    session: AsyncSession,
    *,
    broker_account_id: uuid.UUID,
    symbol_dates: list[tuple[str, date]],
    horizons_months: tuple[int, ...] = DEFAULT_HORIZONS_MONTHS,
    model_version: str = DEFAULT_MODEL_VERSION,
) -> WalkForwardRunResult:
    account_repo = BrokerAccountRepository(session)
    account = await account_repo.get(broker_account_id)
    if account is None:
        raise ValueError(f"No BrokerAccount with id={broker_account_id}")

    run_repo = WalkForwardRunRepository(session)
    run = await run_repo.create(
        WalkForwardRunCreate(
            broker_account_id=broker_account_id,
            strategy_profile=account.strategy_profile,
            horizons_months=list(horizons_months),
            model_version=model_version,
        )
    )

    result = WalkForwardRunResult(run=run)
    for symbol, decision_at in symbol_dates:
        for decision_source in ("ACTUAL", "HOLD_BASELINE"):
            decision = await freeze_decision(
                session,
                run=run,
                broker_account_id=broker_account_id,
                symbol=symbol,
                decision_at=decision_at,
                decision_source=decision_source,
            )
            outcome = await score_outcome(session, decision=decision)
            result.entries.append(WalkForwardEntry(decision=decision, outcome=outcome))

    return result


async def run_bulk_walk_forward(
    session: AsyncSession,
    *,
    broker_account_id: uuid.UUID,
    horizons_months: tuple[int, ...] = DEFAULT_HORIZONS_MONTHS,
    model_version: str = DEFAULT_MODEL_VERSION,
) -> WalkForwardRunResult:
    """Phase 6D: same freeze/score pipeline as run_walk_forward, just fed
    every genuine (symbol, trade_date) pair from this account's real trade
    history instead of a hand-picked list -- no new decision logic."""
    symbol_dates = await generate_decision_points(session, broker_account_id)
    return await run_walk_forward(
        session,
        broker_account_id=broker_account_id,
        symbol_dates=symbol_dates,
        horizons_months=horizons_months,
        model_version=model_version,
    )


async def replay_deterministic_baseline(
    session: AsyncSession, *, run: WalkForwardRun
) -> WalkForwardRunResult:
    """Add offline AGENT comparator decisions to an existing frozen run.

    The source points come only from already-frozen ACTUAL events; no outcome
    fields are read before the baseline decision is made.
    """
    from investing_agent.db.repositories.walkforward import WalkForwardDecisionRepository

    decisions = await WalkForwardDecisionRepository(session).list_for_run(run.id)
    actuals = [d for d in decisions if d.decision_source == "ACTUAL"]
    result = WalkForwardRunResult(run=run)
    for actual in actuals:
        agent = await freeze_deterministic_baseline_decision(
            session,
            run=run,
            broker_account_id=run.broker_account_id,
            symbol=actual.symbol,
            decision_at=actual.decision_at,
        )
        outcome = await score_outcome(session, decision=agent)
        result.entries.append(WalkForwardEntry(decision=agent, outcome=outcome))
    return result
