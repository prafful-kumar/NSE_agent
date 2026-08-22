"""Phase 7F: PIT-aware current broker-account context for recommendations."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import DailyPrice, WatchlistItem
from investing_agent.db.repositories.broker_history import BrokerAccountRepository
from investing_agent.services.reconstruction.service import get_portfolio_as_of

CURRENT_PRICE_MAX_AGE_DAYS = 7


@dataclass(frozen=True)
class ResolvedHolding:
    symbol: str
    quantity: Decimal
    average_price: Decimal
    last_price: Decimal | None
    current_value: Decimal | None
    portfolio_weight_pct: Decimal | None
    price_date: date | None
    provenance: str = "HELD_RECONSTRUCTED"


@dataclass(frozen=True)
class RecommendationAccountContext:
    broker_account_id: uuid.UUID
    broker: str
    account_label: str
    strategy_profile: str
    as_of_date: date
    holdings: dict[str, ResolvedHolding]
    watchlist_symbols: set[str]
    total_market_value: Decimal | None
    market_value_complete: bool
    cash_balance_partial: Decimal
    cash_balance_caveat: str

    @property
    def symbols(self) -> list[str]:
        return sorted(set(self.holdings) | self.watchlist_symbols)


async def resolve_account_context(
    session: AsyncSession,
    *,
    user_id: str,
    broker: str,
    account_label: str,
    as_of_date: date,
) -> RecommendationAccountContext:
    """Resolve held and watchlist universe without using live broker calls.

    Holdings are replayed from immutable Zerodha trade facts up to ``as_of``;
    prices use only the latest archived DailyPrice on or before that date.
    Cash remains explicitly partial because the historical cash ledger does.
    """
    account = await BrokerAccountRepository(session).get_by_label(user_id, broker, account_label)
    if account is None:
        raise ValueError("broker_account_not_found")
    if broker != "ZERODHA" or account.strategy_profile != "LONG_TERM":
        raise ValueError("only_current_zerodha_long_term_context_is_supported")

    reconstructed = await get_portfolio_as_of(
        session, broker_account_id=account.id, as_of_date=as_of_date
    )
    prices: dict[str, tuple[Decimal, date]] = {}
    for position in reconstructed.positions:
        price = (await session.execute(
            select(DailyPrice)
            .where(DailyPrice.symbol == position.symbol, DailyPrice.trading_date <= as_of_date)
            .order_by(DailyPrice.trading_date.desc()).limit(1)
        )).scalar_one_or_none()
        if price is not None and (as_of_date - price.trading_date).days <= CURRENT_PRICE_MAX_AGE_DAYS:
            prices[position.symbol] = (price.close, price.trading_date)

    unweighted: list[ResolvedHolding] = []
    for position in reconstructed.positions:
        price_value = prices.get(position.symbol)
        current_value = position.quantity_held * price_value[0] if price_value else None
        unweighted.append(ResolvedHolding(
            symbol=position.symbol,
            quantity=position.quantity_held,
            average_price=position.average_cost,
            last_price=price_value[0] if price_value else None,
            current_value=current_value,
            portfolio_weight_pct=None,
            price_date=price_value[1] if price_value else None,
        ))
    total = sum((row.current_value for row in unweighted if row.current_value is not None), Decimal("0"))
    market_value_complete = bool(unweighted) and all(row.current_value is not None for row in unweighted)
    holdings = {
        row.symbol: ResolvedHolding(
            **{**row.__dict__, "portfolio_weight_pct": (row.current_value / total * Decimal("100")) if row.current_value is not None and total else None}
        )
        for row in unweighted
    }
    watchlist = list((await session.execute(
        select(WatchlistItem).where(WatchlistItem.user_id == user_id, WatchlistItem.is_active.is_(True))
    )).scalars())
    return RecommendationAccountContext(
        broker_account_id=account.id, broker=account.broker, account_label=account.account_label,
        strategy_profile=account.strategy_profile, as_of_date=as_of_date, holdings=holdings,
        watchlist_symbols={row.symbol.upper() for row in watchlist}, total_market_value=total if total else None,
        market_value_complete=market_value_complete,
        cash_balance_partial=reconstructed.cash_balance_partial,
        cash_balance_caveat=reconstructed.cash_balance_caveat,
    )
