from __future__ import annotations

"""Verification: the gate between "an API returned a value" and "we trust
this as a confirmed fact."

A normalized CorporateActionCreate/FinancialResultCreate always starts with
verification_status="unverified" (the schema default) regardless of
data_category. This service is the ONLY place that flips it to "verified",
and only when there's actual corroborating evidence — never because a
request succeeded. Today's only working corroboration path is cross_source
(comparing NSE against a second independent source for the same fact);
structured_xbrl/parser/manual are real enum values for future paths (once a
verified XBRL/PDF document-discovery adapter exists) but are not exercised
by any adapter in Phase 3A — calling them without real backing would violate
the rule they exist to enforce.
"""

from datetime import UTC, datetime

from investing_agent.schemas.corporate_actions import CorporateActionCreate
from investing_agent.schemas.financials import FinancialResultCreate


def verify_corporate_action_cross_source(
    primary: CorporateActionCreate,
    candidates: list[CorporateActionCreate],
) -> CorporateActionCreate:
    """Looks for a candidate from a DIFFERENT source_type describing the same
    action_type + ex_date (+ matching amount when both have one). If found,
    marks primary as verified via cross_source. Otherwise returns primary
    unchanged (still unverified) — no match is not an error, just no
    corroboration yet.
    """
    for candidate in candidates:
        if candidate.source_type == primary.source_type:
            continue
        if candidate.action_type != primary.action_type:
            continue
        if candidate.ex_date != primary.ex_date:
            continue
        amounts_differ = (
            primary.amount is not None
            and candidate.amount is not None
            and primary.amount != candidate.amount
        )
        if amounts_differ:
            continue  # same date, different amount — a real discrepancy, not a match
        return CorporateActionCreate(
            **{
                **primary.model_dump(),
                "verification_status": "verified",
                "verification_method": "cross_source",
                "verified_at": datetime.now(UTC),
                "verification_notes": f"matched against {candidate.source_type}",
            }
        )
    return primary


def verify_financial_result_cross_source(
    primary: FinancialResultCreate,
    candidates: list[FinancialResultCreate],
) -> FinancialResultCreate:
    """Same idea for financial results: matches on result_date + pat across
    a different source_type."""
    for candidate in candidates:
        if candidate.source_type == primary.source_type:
            continue
        if candidate.result_date != primary.result_date:
            continue
        if primary.pat is not None and candidate.pat is not None and primary.pat != candidate.pat:
            continue
        return FinancialResultCreate(
            **{
                **primary.model_dump(),
                "verification_status": "verified",
                "verification_method": "cross_source",
                "verified_at": datetime.now(UTC),
                "verification_notes": f"matched against {candidate.source_type}",
            }
        )
    return primary
