from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import Company
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.company import CompanyCreate


class CompanyRepository(BaseRepository[Company]):
    model = Company

    async def get_by_symbol(self, symbol: str) -> Company | None:
        result = await self.session.execute(
            select(Company).where(Company.symbol == symbol.upper())
        )
        return result.scalar_one_or_none()

    async def get_by_isin(self, isin: str) -> Company | None:
        result = await self.session.execute(
            select(Company).where(Company.isin == isin)
        )
        return result.scalar_one_or_none()

    async def upsert(self, data: CompanyCreate) -> Company:
        """Insert or update by symbol."""
        existing = await self.get_by_symbol(data.symbol)
        if existing:
            existing.name = data.name
            existing.exchange = data.exchange
            existing.sector = data.sector
            existing.industry = data.industry
            existing.market_cap_category = data.market_cap_category
            existing.is_active = data.is_active
            if data.isin:
                existing.isin = data.isin
            await self.session.flush()
            return existing

        company = Company(
            symbol=data.symbol.upper(),
            isin=data.isin,
            name=data.name,
            exchange=data.exchange,
            sector=data.sector,
            industry=data.industry,
            market_cap_category=data.market_cap_category,
            is_active=data.is_active,
        )
        return await self.add(company)

    async def get_active(self) -> list[Company]:
        result = await self.session.execute(
            select(Company).where(Company.is_active.is_(True))
        )
        return list(result.scalars().all())
