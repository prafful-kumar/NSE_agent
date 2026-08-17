from __future__ import annotations

"""InstrumentResolver: map broker holding data to internal company_id.

Resolution priority:
  1. ISIN lookup in instrument_master
  2. tradingsymbol+exchange lookup in instrument_master
  3. ISIN lookup in companies (by isin column)
  4. tradingsymbol lookup in companies (as symbol)
  5. Create new company stub (for unknown instruments)

All resolutions are recorded in instrument_master for future lookups.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.config.logging import get_logger
from investing_agent.db.models import Company
from investing_agent.db.repositories.company import CompanyRepository
from investing_agent.db.repositories.instrument import InstrumentRepository
from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.instruments import InstrumentCreate, ResolvedInstrument

log = get_logger(__name__)


class InstrumentResolver:
    """Resolves broker holding dicts to internal company_id."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._instrument_repo = InstrumentRepository(session)
        self._company_repo = CompanyRepository(session)

    async def resolve(self, holding: dict[str, Any]) -> ResolvedInstrument:
        """Resolve a single broker holding dict to internal identity.

        Args:
            holding: raw dict from PortfolioReader (Zerodha or mock format).

        Returns:
            ResolvedInstrument with company_id if found, else None.
        """
        symbol = holding.get("tradingsymbol", "")
        exchange = holding.get("exchange", "NSE")
        isin = holding.get("isin") or None
        instrument_type = holding.get("instrument_type", "EQ")

        # ── Step 1: ISIN lookup in instrument_master ──────────────────────────
        if isin:
            instr = await self._instrument_repo.get_by_isin(isin)
            if instr:
                await self._instrument_repo.upsert(InstrumentCreate(
                    tradingsymbol=symbol, exchange=exchange, isin=isin,
                    instrument_type=instrument_type,
                ))
                log.debug("instrument_resolver.isin_hit", symbol=symbol, isin=isin)
                return ResolvedInstrument(
                    tradingsymbol=symbol, exchange=exchange, isin=isin,
                    instrument_type=instrument_type,
                    company_id=instr.company_id,
                    company_symbol=symbol,
                    resolution_method="isin",
                )

        # ── Step 2: symbol+exchange lookup in instrument_master ───────────────
        instr = await self._instrument_repo.get_by_symbol_exchange(symbol, exchange)
        if instr:
            log.debug("instrument_resolver.symbol_hit", symbol=symbol)
            return ResolvedInstrument(
                tradingsymbol=symbol, exchange=exchange,
                isin=instr.isin or isin,
                instrument_type=instrument_type,
                company_id=instr.company_id,
                company_symbol=symbol,
                resolution_method="symbol_exchange",
            )

        # ── Step 3: ISIN lookup in companies ──────────────────────────────────
        company: Company | None = None
        if isin:
            company = await self._company_repo.get_by_isin(isin)
            if company:
                log.debug("instrument_resolver.company_isin_hit", symbol=symbol, isin=isin)
                await self._upsert_instrument(symbol, exchange, isin, instrument_type, company.id)
                return ResolvedInstrument(
                    tradingsymbol=symbol, exchange=exchange, isin=isin,
                    instrument_type=instrument_type,
                    company_id=company.id,
                    company_symbol=company.symbol,
                    resolution_method="isin",
                )

        # ── Step 4: symbol lookup in companies ────────────────────────────────
        company = await self._company_repo.get_by_symbol(symbol)
        if company:
            log.debug("instrument_resolver.company_symbol_hit", symbol=symbol)
            await self._upsert_instrument(symbol, exchange, isin, instrument_type, company.id)
            return ResolvedInstrument(
                tradingsymbol=symbol, exchange=exchange, isin=isin,
                instrument_type=instrument_type,
                company_id=company.id,
                company_symbol=company.symbol,
                resolution_method="symbol_exchange",
            )

        # ── Step 5: create stub company + instrument ───────────────────────────
        log.info("instrument_resolver.creating_stub", symbol=symbol, isin=isin)
        stub = await self._company_repo.upsert(
            CompanyCreate(
                symbol=symbol.upper(),
                isin=isin,
                name=symbol.upper(),  # placeholder — updated when filings come in
                exchange=exchange,
            )
        )
        await self._upsert_instrument(symbol, exchange, isin, instrument_type, stub.id)
        return ResolvedInstrument(
            tradingsymbol=symbol, exchange=exchange, isin=isin,
            instrument_type=instrument_type,
            company_id=stub.id,
            company_symbol=stub.symbol,
            resolution_method="created_new",
        )

    async def _upsert_instrument(
        self,
        symbol: str,
        exchange: str,
        isin: str | None,
        instrument_type: str,
        company_id: uuid.UUID,
    ) -> None:
        await self._instrument_repo.upsert(InstrumentCreate(
            tradingsymbol=symbol,
            exchange=exchange,
            isin=isin,
            instrument_type=instrument_type,
            company_id=company_id,
        ))
