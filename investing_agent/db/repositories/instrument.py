from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import Company, InstrumentMaster
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.instruments import InstrumentCreate


class InstrumentRepository(BaseRepository[InstrumentMaster]):
    model = InstrumentMaster

    async def get_by_isin(self, isin: str) -> InstrumentMaster | None:
        result = await self.session.execute(
            select(InstrumentMaster).where(InstrumentMaster.isin == isin)
        )
        return result.scalar_one_or_none()

    async def get_by_symbol_exchange(
        self, tradingsymbol: str, exchange: str
    ) -> InstrumentMaster | None:
        result = await self.session.execute(
            select(InstrumentMaster).where(
                InstrumentMaster.tradingsymbol == tradingsymbol.upper(),
                InstrumentMaster.exchange == exchange.upper(),
            )
        )
        return result.scalar_one_or_none()

    async def upsert(self, data: InstrumentCreate) -> InstrumentMaster:
        """Insert or update by tradingsymbol+exchange. Update ISIN/token if known."""
        existing = await self.get_by_symbol_exchange(data.tradingsymbol, data.exchange)
        now = datetime.now(timezone.utc)

        if existing:
            if data.isin and existing.isin != data.isin:
                existing.isin = data.isin
            if data.zerodha_instrument_token:
                existing.zerodha_instrument_token = data.zerodha_instrument_token
            if data.company_id and not existing.company_id:
                existing.company_id = data.company_id
            existing.is_active = True
            existing.last_seen = now
            await self.session.flush()
            return existing

        instrument = InstrumentMaster(
            tradingsymbol=data.tradingsymbol.upper(),
            exchange=data.exchange.upper(),
            isin=data.isin,
            instrument_type=data.instrument_type,
            zerodha_instrument_token=data.zerodha_instrument_token,
            company_id=data.company_id,
            is_active=True,
            last_seen=now,
        )
        return await self.add(instrument)

    async def link_company(
        self, instrument_id: uuid.UUID, company_id: uuid.UUID
    ) -> None:
        instr = await self.get(instrument_id)
        if instr:
            instr.company_id = company_id
            await self.session.flush()
