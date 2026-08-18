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

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import FinancialResult
from investing_agent.db.repositories.financial import FinancialResultRepository
from investing_agent.db.repositories.source_document import SourceDocumentRepository
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


@dataclass
class ManualVerificationOutcome:
    result: FinancialResult
    matched: bool
    checked_fields: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    timestamp_upgraded: bool = False


_DEFAULT_TOLERANCE_PCT = Decimal("0.5")


def _within_tolerance(stored: Decimal, reported: Decimal, tolerance_pct: Decimal) -> bool:
    if stored == 0:
        return reported == 0
    return abs((reported - stored) / stored) * 100 <= tolerance_pct


async def verify_financial_result_manual(
    session: AsyncSession,
    *,
    result_id,
    source_document_id,
    reported_revenue: Decimal | None = None,
    reported_pat: Decimal | None = None,
    reported_eps_diluted: Decimal | None = None,
    tolerance_pct: Decimal = _DEFAULT_TOLERANCE_PCT,
) -> ManualVerificationOutcome:
    """The other half of the verification gate: a human has read a primary
    document (NSE archived filing preferred, company IR PDF, BSE/company
    primary document as fallback — never a secondary financial website) and
    is reporting what it says. This function only does the comparison and
    the resulting status flip; it never fetches or parses the document
    itself.

    Never promotes to "verified" on the strength of the API value alone —
    at least one field must actually be supplied and comparable. A
    mismatch against the stored NSE structured-API value is itself a
    meaningful finding (the empirically-documented BEL re_con_pro_loss
    scope mislabeling is exactly this kind of discrepancy) and is recorded
    as "disputed", not silently dropped.
    """
    result_repo = FinancialResultRepository(session)
    row = await result_repo.get(result_id)
    if row is None:
        raise ValueError(f"financial_result {result_id} not found")

    doc = await SourceDocumentRepository(session).get(source_document_id)
    if doc is None:
        raise ValueError(f"source_document {source_document_id} not found")
    if doc.company_id != row.company_id:
        raise ValueError("source_document belongs to a different company than the result")
    if doc.source_type == "nse_json_hint":
        raise ValueError(
            "cannot verify against the same undocumented JSON hint source being checked — "
            "provide an archived primary filing/document instead"
        )

    comparisons = (
        ("revenue", row.revenue, reported_revenue),
        ("pat", row.pat, reported_pat),
        ("eps_diluted", row.eps_diluted, reported_eps_diluted),
    )
    checked_fields: list[str] = []
    mismatches: list[str] = []
    for label, stored, reported in comparisons:
        if reported is None:
            continue
        if stored is None:
            mismatches.append(f"{label}: no stored value to compare against {reported}")
            continue
        checked_fields.append(label)
        if not _within_tolerance(Decimal(str(stored)), Decimal(str(reported)), tolerance_pct):
            mismatches.append(f"{label}: stored={stored} primary_document={reported}")

    if not checked_fields and not mismatches:
        raise ValueError(
            "at least one of reported_revenue/reported_pat/reported_eps_diluted must be provided"
        )

    matched = not mismatches
    now = datetime.now(UTC)
    if matched:
        notes = f"Matched {', '.join(checked_fields)} against document {source_document_id} within {tolerance_pct}% tolerance."
    else:
        notes = "; ".join(mismatches)

    updated = await result_repo.set_verification(
        result_id,
        verification_status="verified" if matched else "disputed",
        verification_document_id=source_document_id,
        verified_at=now,
        verification_method="manual_primary_document",
        verification_notes=notes,
    )

    timestamp_upgraded = False
    if doc.published_at is not None:
        updated = await result_repo.upgrade_timestamp_precision(
            result_id, published_at=doc.published_at, available_at=doc.published_at
        )
        timestamp_upgraded = True

    return ManualVerificationOutcome(
        result=updated,
        matched=matched,
        checked_fields=checked_fields,
        mismatches=mismatches,
        timestamp_upgraded=timestamp_upgraded,
    )


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
