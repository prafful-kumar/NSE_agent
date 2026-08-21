from __future__ import annotations

"""Repositories for Phase 6C walk-forward simulation.

WalkForwardDecision is idempotent on its natural key (run_id, symbol,
decision_at, decision_source) -- create_if_not_exists is the only write path
offered, mirroring HistoricalTrade's dedupe-key discipline; no update method
exists at all, which is itself part of the "immutable frozen decision"
guarantee tested in tests/integration/test_walkforward_integration.py.
WalkForwardOutcome is 1:1 with a decision (unique on decision_id), also
create_if_not_exists rather than an update.
"""

import uuid

from sqlalchemy import select

from investing_agent.db.models import WalkForwardDecision, WalkForwardOutcome, WalkForwardRun
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.walkforward import (
    WalkForwardDecisionCreate,
    WalkForwardOutcomeCreate,
    WalkForwardRunCreate,
)


class WalkForwardRunRepository(BaseRepository[WalkForwardRun]):
    model = WalkForwardRun

    async def create(self, data: WalkForwardRunCreate) -> WalkForwardRun:
        row = WalkForwardRun(**data.model_dump())
        return await self.add(row)

    async def latest_for_account(self, broker_account_id: uuid.UUID) -> WalkForwardRun | None:
        result = await self.session.execute(
            select(WalkForwardRun)
            .where(WalkForwardRun.broker_account_id == broker_account_id)
            .order_by(WalkForwardRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class WalkForwardDecisionRepository(BaseRepository[WalkForwardDecision]):
    model = WalkForwardDecision

    async def get_by_natural_key(
        self, run_id: uuid.UUID, symbol: str, decision_at, decision_source: str
    ) -> WalkForwardDecision | None:
        result = await self.session.execute(
            select(WalkForwardDecision).where(
                WalkForwardDecision.run_id == run_id,
                WalkForwardDecision.symbol == symbol,
                WalkForwardDecision.decision_at == decision_at,
                WalkForwardDecision.decision_source == decision_source,
            )
        )
        return result.scalar_one_or_none()

    async def create_if_not_exists(
        self, data: WalkForwardDecisionCreate
    ) -> tuple[WalkForwardDecision, bool]:
        existing = await self.get_by_natural_key(
            data.run_id, data.symbol, data.decision_at, data.decision_source
        )
        if existing is not None:
            return existing, False
        row = WalkForwardDecision(**data.model_dump())
        return await self.add(row), True

    async def list_for_run(self, run_id: uuid.UUID) -> list[WalkForwardDecision]:
        result = await self.session.execute(
            select(WalkForwardDecision)
            .where(WalkForwardDecision.run_id == run_id)
            .order_by(WalkForwardDecision.symbol, WalkForwardDecision.decision_at)
        )
        return list(result.scalars().all())


class WalkForwardOutcomeRepository(BaseRepository[WalkForwardOutcome]):
    model = WalkForwardOutcome

    async def get_by_decision_id(self, decision_id: uuid.UUID) -> WalkForwardOutcome | None:
        result = await self.session.execute(
            select(WalkForwardOutcome).where(WalkForwardOutcome.decision_id == decision_id)
        )
        return result.scalar_one_or_none()

    async def create_if_not_exists(
        self, data: WalkForwardOutcomeCreate
    ) -> tuple[WalkForwardOutcome, bool]:
        existing = await self.get_by_decision_id(data.decision_id)
        if existing is not None:
            return existing, False
        row = WalkForwardOutcome(**data.model_dump())
        return await self.add(row), True
