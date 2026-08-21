from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from investing_agent.schemas.common import BaseSchema, TimestampedSchema

CandidatePolicyStatus = Literal["PROPOSED", "BACKTESTED", "REJECTED", "APPROVED"]
ProposalDecision = Literal["APPROVE", "REJECT"]


class CandidatePolicyRuleCreate(BaseSchema):
    broker_account_id: uuid.UUID
    rule_id: str
    strategy_profile: str
    feature_condition: dict[str, Any]
    affected_action: str
    proposed_adjustment: dict[str, Any]
    evidence_window: dict[str, Any]
    sample_size: int
    supporting_metrics: dict[str, Any]
    confidence: Decimal | None = None
    status: CandidatePolicyStatus = "PROPOSED"


class CandidatePolicyRuleRead(CandidatePolicyRuleCreate, TimestampedSchema):
    id: uuid.UUID
    approved_at: datetime | None


class PolicyProposalCreate(BaseSchema):
    candidate_rule_id: uuid.UUID
    current_behavior: str
    historical_evidence: str
    proposed_adjustment: str
    expected_benefit: str
    known_risks: str
    out_of_sample_result: dict[str, Any]
    decision: ProposalDecision


class PolicyProposalRead(PolicyProposalCreate, TimestampedSchema):
    id: uuid.UUID
