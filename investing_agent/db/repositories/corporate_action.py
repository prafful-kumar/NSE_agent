from __future__ import annotations

"""Repository for corporate_actions — versioned upsert + calendar queries.

Same versioning contract as FinancialResultRepository: identical
re-ingestion is a no-op, a genuine change (postponed AGM, revised dividend
amount) creates a new version and preserves the old one. Calendar queries
support an as_of parameter so a caller can ask "what did we know as of date
X" without ever seeing rows that became available_at after that instant —
the point-in-time guarantee this table exists to provide.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import select

from investing_agent.db.models import CorporateAction
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.corporate_actions import CorporateActionCreate

_COMPARE_FIELDS = (
    "ex_date", "record_date", "payment_date", "agm_date", "amount",
    "amount_currency", "ratio", "dividend_type", "is_confirmed",
    "board_meeting_announced_at", "board_meeting_date",
    "expected_result_date", "actual_result_published_at",
)


def _values_equal(a: CorporateAction, data: CorporateActionCreate) -> bool:
    for field_name in _COMPARE_FIELDS:
        if getattr(a, field_name) != getattr(data, field_name):
            return False
    return True


class CorporateActionRepository(BaseRepository[CorporateAction]):
    model = CorporateAction

    async def get_latest(
        self, company_id: uuid.UUID, action_type: str, event_date: date, source_type: str
    ) -> CorporateAction | None:
        result = await self.session.execute(
            select(CorporateAction).where(
                CorporateAction.company_id == company_id,
                CorporateAction.action_type == action_type,
                CorporateAction.event_date == event_date,
                CorporateAction.source_type == source_type,
                CorporateAction.is_latest.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def upsert_versioned(self, data: CorporateActionCreate) -> tuple[CorporateAction, bool]:
        existing = await self.get_latest(
            data.company_id, data.action_type, data.event_date, data.source_type
        )

        if existing and _values_equal(existing, data):
            return existing, False

        version = (existing.version + 1) if existing else 1
        row = CorporateAction(
            company_id=data.company_id,
            symbol=data.symbol,
            action_type=data.action_type,
            announced_date=data.announced_date,
            event_date=data.event_date,
            ex_date=data.ex_date,
            record_date=data.record_date,
            payment_date=data.payment_date,
            agm_date=data.agm_date,
            amount=data.amount,
            amount_currency=data.amount_currency,
            ratio=data.ratio,
            dividend_type=data.dividend_type,
            details=data.details,
            is_confirmed=data.is_confirmed,
            board_meeting_announced_at=data.board_meeting_announced_at,
            board_meeting_date=data.board_meeting_date,
            expected_result_date=data.expected_result_date,
            actual_result_published_at=data.actual_result_published_at,
            version=version,
            is_latest=True,
            supersedes_id=existing.id if existing else None,
            source_type=data.source_type,
            source_url=data.source_url,
            published_at=data.published_at,
            data_category=data.data_category,
            confidence=data.confidence,
            source_document_id=data.source_document_id,
            verification_status=data.verification_status,
            verification_document_id=data.verification_document_id,
            verified_at=data.verified_at,
            verification_method=data.verification_method,
            verification_notes=data.verification_notes,
        )
        await self.add(row)

        if existing:
            existing.is_latest = False
            await self.session.flush()

        return row, True

    async def list_by_company(
        self, company_id: uuid.UUID, action_type: str | None = None
    ) -> list[CorporateAction]:
        stmt = select(CorporateAction).where(
            CorporateAction.company_id == company_id,
            CorporateAction.is_latest.is_(True),
        )
        if action_type:
            stmt = stmt.where(CorporateAction.action_type == action_type)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_company_as_of(
        self, company_id: uuid.UUID, as_of: datetime
    ) -> list[CorporateAction]:
        """Point-in-time view: highest version per (action_type, event_date,
        source_type) whose available_at <= as_of."""
        result = await self.session.execute(
            select(CorporateAction).where(
                CorporateAction.company_id == company_id,
                CorporateAction.available_at <= as_of,
            )
        )
        rows = list(result.scalars().all())
        latest_per_group: dict[tuple, CorporateAction] = {}
        for row in rows:
            key = (row.action_type, row.event_date, row.source_type)
            current = latest_per_group.get(key)
            if current is None or row.version > current.version:
                latest_per_group[key] = row
        return list(latest_per_group.values())

    async def get_dividend_calendar(
        self,
        company_ids: list[uuid.UUID] | None = None,
        days: int = 30,
        as_of: datetime | None = None,
    ) -> list[CorporateAction]:
        """Dividends with ex_date within [today, today+days], restricted to
        facts available_at <= as_of (defaults to now — never sees future
        ingestion)."""
        today = (as_of or datetime.now()).date()
        from datetime import timedelta

        horizon = today + timedelta(days=days)

        stmt = select(CorporateAction).where(
            CorporateAction.action_type == "dividend",
            CorporateAction.is_latest.is_(True),
            CorporateAction.ex_date.is_not(None),
            CorporateAction.ex_date >= today,
            CorporateAction.ex_date <= horizon,
        )
        if as_of:
            stmt = stmt.where(CorporateAction.available_at <= as_of)
        if company_ids:
            stmt = stmt.where(CorporateAction.company_id.in_(company_ids))
        result = await self.session.execute(stmt.order_by(CorporateAction.ex_date.asc()))
        return list(result.scalars().all())

    async def get_result_calendar(
        self,
        company_ids: list[uuid.UUID] | None = None,
        as_of: datetime | None = None,
    ) -> list[CorporateAction]:
        """Board-meeting/result rows with an expected_result_date in the
        future relative to as_of, restricted to facts available at that
        instant."""
        today = (as_of or datetime.now()).date()

        stmt = select(CorporateAction).where(
            CorporateAction.is_latest.is_(True),
            CorporateAction.expected_result_date.is_not(None),
            CorporateAction.expected_result_date >= today,
        )
        if as_of:
            stmt = stmt.where(CorporateAction.available_at <= as_of)
        if company_ids:
            stmt = stmt.where(CorporateAction.company_id.in_(company_ids))
        result = await self.session.execute(
            stmt.order_by(CorporateAction.expected_result_date.asc())
        )
        return list(result.scalars().all())
