from __future__ import annotations

"""Decision-point generation for Phase 6D bulk walk-forward evaluation.

generate_decision_points reads real, already-recorded HistoricalTrade rows
and returns every (symbol, trade_date) pair the account genuinely traded --
the same candidate list a human would get by looking at the tradebook. It
invents nothing: no synthetic dates, no sampling, no filtering by outcome.
Exclusion (unreliable reconstruction, missing prices, unresolved corporate
actions, insufficient horizon) happens downstream, per-decision, in
services/walkforward/audit.py -- after freeze_decision/score_outcome have
already run with their existing, unmodified rules.
"""

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.repositories.broker_history import HistoricalTradeRepository


async def generate_decision_points(
    session: AsyncSession, broker_account_id: uuid.UUID
) -> list[tuple[str, date]]:
    trade_repo = HistoricalTradeRepository(session)
    return await trade_repo.list_distinct_symbol_dates(broker_account_id)
