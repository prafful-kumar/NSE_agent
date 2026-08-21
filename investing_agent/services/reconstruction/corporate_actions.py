from __future__ import annotations

"""Loads/records the two Phase 6B adjustment mechanisms that make FIFO
reconstruction (fifo.py) coherent across known data gaps:

- Corporate actions (splits/bonuses) are read from the existing
  corporate_actions table (Phase 3B's CorporateAction model, action_type
  IN ("split","bonus")) rather than a new table -- that table already
  carries exactly this fact (symbol, event_date, ratio) plus provenance/
  versioning, so reconstruction reads it directly instead of duplicating
  it. ratio is stored there as a free-text "old:new" string (Zerodha/NSE
  convention, e.g. "1:10"); _parse_ratio turns it into the numeric
  ratio_old/ratio_new pair fifo.py needs to do the actual math.

- Opening-position adjustments (a reconciliation-derived estimate of a
  missing acquisition trade, e.g. ZAGGLE's SELL with no prior BUY) are
  account-scoped and have no Phase 3B analogue, so they get their own
  table (OpeningPositionAdjustment, migration 013).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import CorporateAction
from investing_agent.db.models import OpeningPositionAdjustment as OpeningPositionAdjustmentRow
from investing_agent.db.repositories.broker_history import OpeningPositionAdjustmentRepository
from investing_agent.db.repositories.corporate_action import CorporateActionRepository
from investing_agent.schemas.corporate_actions import (
    AdjustmentConfidence,
    CorporateActionCreate,
    OpeningPositionAdjustmentCreate,
)
from investing_agent.services.ingestion.common import ensure_company
from investing_agent.services.reconstruction import fifo


def parse_ratio(ratio: str | None) -> tuple[Decimal, Decimal] | None:
    """"old:new" -> (ratio_old, ratio_new). Returns None if unparseable so
    a malformed/manually-entered ratio degrades to "not applied" rather
    than a crash -- it still shows up in the corporate-actions calendar,
    just not in FIFO reconstruction."""
    if not ratio or ":" not in ratio:
        return None
    old_str, new_str = ratio.split(":", 1)
    try:
        return Decimal(old_str.strip()), Decimal(new_str.strip())
    except Exception:
        return None


# Backward-compatible alias for the original private name.
_parse_ratio = parse_ratio


async def load_corporate_action_events(
    session: AsyncSession, as_of: datetime | None = None
) -> list[fifo.CorporateActionEvent]:
    """as_of=None (default) preserves exact current behavior: every
    existing Phase 6B caller sees the current latest version of every
    split/bonus. as_of given restricts to versions whose available_at <=
    as_of -- the point-in-time-safe path used by Phase 6C's decision
    freeze."""
    repo = CorporateActionRepository(session)
    rows = (
        await repo.list_all_splits_and_bonuses_as_of(as_of)
        if as_of is not None
        else await repo.list_all_splits_and_bonuses()
    )
    events: list[fifo.CorporateActionEvent] = []
    for row in rows:
        parsed = parse_ratio(row.ratio)
        if parsed is None:
            continue
        ratio_old, ratio_new = parsed
        events.append(
            fifo.CorporateActionEvent(
                symbol=row.symbol,
                event_type="SPLIT" if row.action_type == "split" else "BONUS",
                event_date=row.event_date,
                ratio_new=ratio_new,
                ratio_old=ratio_old,
                source=row.source_type,
            )
        )
    return events


async def load_opening_adjustments(
    session: AsyncSession, broker_account_id: uuid.UUID
) -> list[fifo.OpeningPositionAdjustment]:
    repo = OpeningPositionAdjustmentRepository(session)
    rows = await repo.list_for_account(broker_account_id)
    return [
        fifo.OpeningPositionAdjustment(
            symbol=row.symbol,
            isin=row.isin,
            company_id=row.company_id,
            opening_date=row.opening_date,
            quantity=row.quantity,
            cost_price=row.cost_price,
            source=row.source,
            confidence=row.confidence,
            reason=row.reason,
        )
        for row in rows
    ]


async def record_corporate_action_event(
    session: AsyncSession,
    *,
    symbol: str,
    event_type: Literal["SPLIT", "BONUS"],
    event_date: date,
    ratio_new: Decimal,
    ratio_old: Decimal,
    source: str,
    source_url: str | None = None,
    notes: str | None = None,
) -> tuple[CorporateAction, bool]:
    company = await ensure_company(session, symbol)
    repo = CorporateActionRepository(session)
    data = CorporateActionCreate(
        company_id=company.id,
        symbol=symbol.upper(),
        action_type=event_type.lower(),
        event_date=event_date,
        ratio=f"{ratio_old}:{ratio_new}",
        details={"notes": notes} if notes else None,
        is_confirmed=True,
        source_type=source,
        source_url=source_url,
        data_category="fact",
    )
    return await repo.upsert_versioned(data)


async def record_opening_position_adjustment(
    session: AsyncSession,
    *,
    broker_account_id: uuid.UUID,
    symbol: str,
    opening_date: date,
    quantity: Decimal,
    cost_price: Decimal,
    source: str,
    confidence: AdjustmentConfidence,
    reason: str,
    isin: str | None = None,
    notes: str | None = None,
) -> tuple[OpeningPositionAdjustmentRow, bool]:
    company = await ensure_company(session, symbol)
    repo = OpeningPositionAdjustmentRepository(session)
    data = OpeningPositionAdjustmentCreate(
        broker_account_id=broker_account_id,
        symbol=symbol.upper(),
        isin=isin,
        company_id=company.id,
        opening_date=opening_date,
        quantity=quantity,
        cost_price=cost_price,
        source=source,
        confidence=confidence,
        reason=reason,
        notes=notes,
    )
    return await repo.upsert(data)
