from __future__ import annotations
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from investing_agent.db.models import Holding, PortfolioSnapshot
from investing_agent.db.repositories.base import BaseRepository
from investing_agent.schemas.portfolio import BrokerPortfolioResponse


class PortfolioRepository(BaseRepository[PortfolioSnapshot]):
    model = PortfolioSnapshot

    async def get_latest(self, user_id: str) -> PortfolioSnapshot | None:
        result = await self.session.execute(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.user_id == user_id)
            .options(selectinload(PortfolioSnapshot.holdings))
            .order_by(desc(PortfolioSnapshot.snapshot_date), desc(PortfolioSnapshot.created_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def save_from_broker(
        self,
        user_id: str,
        response: BrokerPortfolioResponse,
    ) -> PortfolioSnapshot:
        """Persist a broker portfolio response as a snapshot."""
        today = date.today()

        total_value = Decimal("0")
        total_invested = Decimal("0")
        holdings_data = []

        for h in response.holdings:
            current_value = h.last_price * h.quantity
            invested = h.average_price * h.quantity
            total_value += current_value
            total_invested += invested
            holdings_data.append(h)

        total_pnl = total_value - total_invested
        pnl_pct = total_pnl / total_invested if total_invested else Decimal("0")

        snapshot = PortfolioSnapshot(
            user_id=user_id,
            snapshot_date=today,
            total_value=total_value,
            total_invested=total_invested,
            total_pnl=total_pnl,
            pnl_pct=pnl_pct,
            source=response.source,
            source_timestamp=response.fetched_at,
            ingestion_timestamp=datetime.now(timezone.utc),
            raw_response={"holdings_count": len(response.holdings)},
        )
        self.session.add(snapshot)
        await self.session.flush()

        for h in holdings_data:
            current_value = h.last_price * h.quantity
            invested = h.average_price * h.quantity
            pnl = current_value - invested
            pnl_pct = pnl / invested if invested else Decimal("0")
            weight = current_value / total_value if total_value else Decimal("0")

            holding = Holding(
                snapshot_id=snapshot.id,
                symbol=h.tradingsymbol,
                isin=h.isin,
                quantity=h.quantity,
                t1_quantity=h.t1_quantity,
                average_price=h.average_price,
                last_price=h.last_price,
                current_value=current_value,
                pnl=pnl,
                pnl_pct=pnl_pct,
                portfolio_weight_pct=weight * 100,
            )
            self.session.add(holding)

        await self.session.flush()
        return snapshot

    async def get_history(
        self, user_id: str, limit: int = 30
    ) -> list[PortfolioSnapshot]:
        """Return recent snapshots ordered by date descending."""
        result = await self.session.execute(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.user_id == user_id)
            .order_by(desc(PortfolioSnapshot.snapshot_date), desc(PortfolioSnapshot.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_portfolio_symbols(self, user_id: str) -> list[str]:
        """Return current portfolio symbols (from latest snapshot)."""
        snapshot = await self.get_latest(user_id)
        if not snapshot:
            return []
        return [h.symbol for h in snapshot.holdings]
