from __future__ import annotations

"""Repositories for financial_periods (dimension) and financial_results
(versioned fact table).

Versioning rule (upsert_versioned): look up the current is_latest row for
the same (period, statement_scope, reporting_basis, source_type). If none
exists, insert version 1. If one exists and the reported values are
unchanged, return it as-is (idempotent re-ingestion, no new row). If values
differ, insert a new row with version+1 and supersedes_id set, and flip the
prior row's is_latest to False — the prior row is never deleted or
overwritten, satisfying "corrected filings preserve prior versions."
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select

from investing_agent.db.models import FinancialPeriod, FinancialResult
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.financials import FinancialResultCreate

_COMPARE_FIELDS = (
    "is_audited", "result_date", "currency", "unit_scale",
    "revenue", "ebitda", "ebitda_margin_pct", "ebitda_source",
    "pbt", "pat", "pat_margin_pct", "eps_basic", "eps_diluted",
    "total_debt", "cash_equivalents", "operating_cash_flow",
    "roe_pct", "roce_pct",
)


class FinancialPeriodRepository(BaseRepository[FinancialPeriod]):
    model = FinancialPeriod

    async def get_or_create(
        self,
        company_id: uuid.UUID,
        period_type: str,
        fiscal_year: int,
        quarter: str | None,
        period_end: date,
        label: str,
        period_start: date | None = None,
    ) -> FinancialPeriod:
        result = await self.session.execute(
            select(FinancialPeriod).where(
                FinancialPeriod.company_id == company_id,
                FinancialPeriod.period_type == period_type,
                FinancialPeriod.fiscal_year == fiscal_year,
                FinancialPeriod.quarter == quarter,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        period = FinancialPeriod(
            company_id=company_id,
            period_type=period_type,
            fiscal_year=fiscal_year,
            quarter=quarter,
            period_start=period_start,
            period_end=period_end,
            label=label,
        )
        return await self.add(period)


def _values_equal(a: FinancialResult, data: FinancialResultCreate) -> bool:
    for field_name in _COMPARE_FIELDS:
        existing_val = getattr(a, field_name)
        new_val = getattr(data, field_name)
        if isinstance(existing_val, Decimal) and new_val is not None:
            new_val = Decimal(str(new_val))
        if existing_val != new_val:
            return False
    return True


class FinancialResultRepository(BaseRepository[FinancialResult]):
    model = FinancialResult

    async def get_latest(
        self, period_id: uuid.UUID, statement_scope: str, reporting_basis: str, source_type: str
    ) -> FinancialResult | None:
        result = await self.session.execute(
            select(FinancialResult).where(
                FinancialResult.period_id == period_id,
                FinancialResult.statement_scope == statement_scope,
                FinancialResult.reporting_basis == reporting_basis,
                FinancialResult.source_type == source_type,
                FinancialResult.is_latest.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def upsert_versioned(self, data: FinancialResultCreate) -> tuple[FinancialResult, bool]:
        """Returns (row, was_new_version)."""
        existing = await self.get_latest(
            data.period_id, data.statement_scope, data.reporting_basis, data.source_type
        )

        if existing and _values_equal(existing, data):
            return existing, False  # idempotent — identical re-ingestion

        version = (existing.version + 1) if existing else 1
        row = FinancialResult(
            period_id=data.period_id,
            company_id=data.company_id,
            symbol=data.symbol,
            statement_scope=data.statement_scope,
            reporting_basis=data.reporting_basis,
            is_audited=data.is_audited,
            result_date=data.result_date,
            currency=data.currency,
            unit_scale=data.unit_scale,
            revenue=data.revenue,
            ebitda=data.ebitda,
            ebitda_margin_pct=data.ebitda_margin_pct,
            ebitda_source=data.ebitda_source,
            pbt=data.pbt,
            pat=data.pat,
            pat_margin_pct=data.pat_margin_pct,
            eps_basic=data.eps_basic,
            eps_diluted=data.eps_diluted,
            total_debt=data.total_debt,
            cash_equivalents=data.cash_equivalents,
            operating_cash_flow=data.operating_cash_flow,
            roe_pct=data.roe_pct,
            roce_pct=data.roce_pct,
            other_metrics=data.other_metrics,
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

    async def get_history(
        self, period_id: uuid.UUID, statement_scope: str, reporting_basis: str, source_type: str
    ) -> list[FinancialResult]:
        """All versions, oldest first — for point-in-time reconstruction."""
        result = await self.session.execute(
            select(FinancialResult)
            .where(
                FinancialResult.period_id == period_id,
                FinancialResult.statement_scope == statement_scope,
                FinancialResult.reporting_basis == reporting_basis,
                FinancialResult.source_type == source_type,
            )
            .order_by(FinancialResult.version.asc())
        )
        return list(result.scalars().all())

    async def get_as_of(
        self,
        period_id: uuid.UUID,
        statement_scope: str,
        reporting_basis: str,
        source_type: str,
        as_of,
    ) -> FinancialResult | None:
        """The version that was available_at <= as_of — point-in-time query.
        Never returns a version published/available after `as_of`."""
        result = await self.session.execute(
            select(FinancialResult)
            .where(
                FinancialResult.period_id == period_id,
                FinancialResult.statement_scope == statement_scope,
                FinancialResult.reporting_basis == reporting_basis,
                FinancialResult.source_type == source_type,
                FinancialResult.available_at <= as_of,
            )
            .order_by(FinancialResult.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_by_company(self, company_id: uuid.UUID) -> list[FinancialResult]:
        result = await self.session.execute(
            select(FinancialResult).where(
                FinancialResult.company_id == company_id,
                FinancialResult.is_latest.is_(True),
            )
        )
        return list(result.scalars().all())

    async def get_earliest_available_at(self, period_id: uuid.UUID) -> datetime | None:
        """The earliest available_at across every FinancialResult row (any
        scope/basis/version) tied to this period — i.e. the moment any actual
        result for this quarter first entered the system. Backtesting derives
        its historical cutoff_at from this value (minus a second), never a
        guessed offset, so a backtest estimate is guaranteed to predate every
        actual it's later scored against."""
        result = await self.session.execute(
            select(func.min(FinancialResult.available_at)).where(
                FinancialResult.period_id == period_id
            )
        )
        return result.scalar_one_or_none()

    async def list_by_company_as_of(self, company_id: uuid.UUID, as_of) -> list[FinancialResult]:
        """Point-in-time view across all periods for a company: for each
        (period, scope, basis, source_type) group, the highest version whose
        available_at <= as_of. Never returns a row that became available
        after as_of — this is what makes "what did we know as of date X"
        answerable."""
        result = await self.session.execute(
            select(FinancialResult).where(
                FinancialResult.company_id == company_id,
                FinancialResult.available_at <= as_of,
            )
        )
        rows = list(result.scalars().all())
        latest_per_group: dict[tuple, FinancialResult] = {}
        for row in rows:
            key = (row.period_id, row.statement_scope, row.reporting_basis, row.source_type)
            current = latest_per_group.get(key)
            if current is None or row.version > current.version:
                latest_per_group[key] = row
        return list(latest_per_group.values())
