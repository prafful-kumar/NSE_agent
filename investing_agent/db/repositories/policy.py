from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from investing_agent.db.models import CandidatePolicyRule, PolicyProposal
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.policy import CandidatePolicyRuleCreate, PolicyProposalCreate


class CandidatePolicyRuleRepository(BaseRepository[CandidatePolicyRule]):
    model = CandidatePolicyRule

    async def get_by_rule_id(self, rule_id: str) -> CandidatePolicyRule | None:
        result = await self.session.execute(
            select(CandidatePolicyRule).where(CandidatePolicyRule.rule_id == rule_id)
        )
        return result.scalar_one_or_none()

    async def list_for_account(self, broker_account_id: uuid.UUID) -> list[CandidatePolicyRule]:
        result = await self.session.execute(
            select(CandidatePolicyRule)
            .where(CandidatePolicyRule.broker_account_id == broker_account_id)
            .order_by(CandidatePolicyRule.created_at)
        )
        return list(result.scalars().all())

    async def create_or_replace_draft(
        self, data: CandidatePolicyRuleCreate
    ) -> CandidatePolicyRule:
        """Refresh an unapproved candidate from a reproducible rerun.

        Approved rules are immutable review records and therefore never
        overwritten by an offline backtest rerun.
        """
        existing = await self.get_by_rule_id(data.rule_id)
        if existing is None:
            return await self.add(CandidatePolicyRule(**data.model_dump()))
        if existing.status == "APPROVED":
            return existing
        for field, value in data.model_dump().items():
            setattr(existing, field, value)
        await self.session.flush()
        return existing

    async def set_status(self, rule: CandidatePolicyRule, status: str) -> CandidatePolicyRule:
        if status == "APPROVED":
            rule.approved_at = datetime.now(UTC)
        rule.status = status
        await self.session.flush()
        return rule


class PolicyProposalRepository(BaseRepository[PolicyProposal]):
    model = PolicyProposal

    async def get_by_candidate_rule_id(self, candidate_rule_id: uuid.UUID) -> PolicyProposal | None:
        result = await self.session.execute(
            select(PolicyProposal).where(PolicyProposal.candidate_rule_id == candidate_rule_id)
        )
        return result.scalar_one_or_none()

    async def create_or_replace(self, data: PolicyProposalCreate) -> PolicyProposal:
        existing = await self.get_by_candidate_rule_id(data.candidate_rule_id)
        if existing is None:
            return await self.add(PolicyProposal(**data.model_dump()))
        for field, value in data.model_dump().items():
            setattr(existing, field, value)
        await self.session.flush()
        return existing
