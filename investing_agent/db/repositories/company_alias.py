from __future__ import annotations

"""Repository for company_aliases — the deterministic name/ticker matching
table used by services/matching/company_matcher.py."""

import uuid

from sqlalchemy import select

from investing_agent.db.models import CompanyAlias
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.news import CompanyAliasCreate


class CompanyAliasRepository(BaseRepository[CompanyAlias]):
    model = CompanyAlias

    async def create(self, data: CompanyAliasCreate) -> CompanyAlias:
        row = CompanyAlias(**data.model_dump())
        return await self.add(row)

    async def list_active(self) -> list[CompanyAlias]:
        result = await self.session.execute(
            select(CompanyAlias).where(CompanyAlias.is_active.is_(True))
        )
        return list(result.scalars().all())

    async def list_by_company(self, company_id: uuid.UUID) -> list[CompanyAlias]:
        result = await self.session.execute(
            select(CompanyAlias).where(CompanyAlias.company_id == company_id)
        )
        return list(result.scalars().all())

    async def get_by_company_and_alias(
        self, company_id: uuid.UUID, alias: str
    ) -> CompanyAlias | None:
        result = await self.session.execute(
            select(CompanyAlias).where(
                CompanyAlias.company_id == company_id, CompanyAlias.alias == alias
            )
        )
        return result.scalar_one_or_none()
