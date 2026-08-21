from __future__ import annotations

"""Pure, DB-agnostic FIFO lot-matching engine (Phase 6B).

Given a chronologically ordered sequence of BUY/SELL events for a single
broker account (across possibly many symbols), replays them lot-by-lot per
symbol: BUY opens a new lot, SELL consumes the oldest open lot(s) first
(FIFO), realizing P&L on each consumed slice. Cost basis is price-only
(charges excluded) -- this matches Zerodha's own P&L convention (verified
against the real pnl-*.xlsx exports: Realized P&L = Sell Value - Buy
Value), so reconciliation against Zerodha's own reports is apples-to-apples.

Oversell (a SELL whose quantity exceeds every open lot for that symbol) is
not treated as an error -- it is the expected signature of an untracked
corporate action (bonus/split/demerger; the Zerodha tradebook importer only
ever emits BUY/SELL rows, see zerodha.py). Rather than crash or silently
misreport quantity, the excess is absorbed into a synthetic zero-cost lot
dated at the sell itself, and a warning is recorded so it surfaces in
reconciliation instead of being swallowed. Once the real corporate action is
known (a CorporateActionEvent) or the missing acquisition is estimated from
reconciliation (an OpeningPositionAdjustment), passing those in resolves the
oversell at its source instead of leaving a zero-cost synthetic lot.

CorporateActionEvent (SPLIT/BONUS) is applied chronologically: when its
event_date is reached in the merged timeline, every currently open lot for
that symbol has quantity_opened/quantity_remaining scaled by
ratio_new/ratio_old and cost_price scaled by the inverse, so invested
capital is conserved across the event. OpeningPositionAdjustment injects a
synthetic BUY-like lot at a given date/quantity/cost_price to stand in for
a real acquisition trade that never made it into the tradebook.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

TradeSide = Literal["BUY", "SELL"]
CorporateActionType = Literal["SPLIT", "BONUS"]
LotOrigin = Literal["TRADE", "OVERSELL_SYNTHETIC", "OPENING_ADJUSTMENT"]


@dataclass(frozen=True)
class TradeEvent:
    dedupe_key: str
    symbol: str
    isin: str | None
    company_id: uuid.UUID | None
    trade_date: date
    trade_datetime: datetime | None
    side: TradeSide
    quantity: Decimal
    price: Decimal


@dataclass(frozen=True)
class CorporateActionEvent:
    symbol: str
    event_type: CorporateActionType
    event_date: date
    ratio_new: Decimal  # e.g. CDSL 1:2 bonus -> ratio_new=2, ratio_old=1
    ratio_old: Decimal
    source: str


@dataclass(frozen=True)
class OpeningPositionAdjustment:
    symbol: str
    isin: str | None
    company_id: uuid.UUID | None
    opening_date: date
    quantity: Decimal
    cost_price: Decimal
    source: str
    confidence: str
    reason: str


@dataclass
class CorporateActionAdjustmentApplied:
    """Audit record of a CorporateActionEvent's effect on this account's
    open lots at the moment it was applied -- preserved (not just the
    abstract ratio) so the actual quantity/cost-basis transformation is
    always visible, per the user's explicit request."""

    symbol: str
    event_type: str
    event_date: date
    ratio_new: Decimal
    ratio_old: Decimal
    old_quantity: Decimal
    new_quantity: Decimal
    old_cost_per_share: Decimal
    adjusted_cost_per_share: Decimal
    source: str


@dataclass
class Lot:
    symbol: str
    isin: str | None
    company_id: uuid.UUID | None
    opened_date: date
    quantity_opened: Decimal
    quantity_remaining: Decimal
    cost_price: Decimal
    opening_trade_dedupe_key: str
    closed_date: date | None = None
    realized_pnl: Decimal = Decimal("0")
    is_synthetic: bool = False  # not a real trade-sourced lot (oversell or opening adjustment)
    lot_origin: LotOrigin = "TRADE"


@dataclass
class FifoReplayResult:
    all_lots: list[Lot] = field(default_factory=list)  # open + closed, every symbol
    realized_pnl_by_symbol: dict[str, Decimal] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    corporate_action_adjustments: list[CorporateActionAdjustmentApplied] = field(default_factory=list)

    def open_lots(self, symbol: str | None = None) -> list[Lot]:
        return [
            lot for lot in self.all_lots
            if lot.quantity_remaining > 0 and (symbol is None or lot.symbol == symbol)
        ]

    def symbols_with_open_lots(self) -> list[str]:
        seen: dict[str, None] = {}
        for lot in self.open_lots():
            seen.setdefault(lot.symbol, None)
        return list(seen.keys())

    def realized_pnl_total(self) -> Decimal:
        return sum(self.realized_pnl_by_symbol.values(), Decimal("0"))


def replay_fifo(
    events: list[TradeEvent],
    *,
    corporate_actions: list[CorporateActionEvent] | None = None,
    opening_adjustments: list[OpeningPositionAdjustment] | None = None,
) -> FifoReplayResult:
    """events must already be sorted chronologically per symbol (trade_date,
    then trade_datetime, then dedupe_key for determinism) -- see replay.py.

    corporate_actions and opening_adjustments are merged into the same
    chronological timeline: on a shared date, opening adjustments apply
    first (so a same-day SELL has a lot to consume), then corporate
    actions, then trades -- see the (date, priority, seq) sort key below.
    """
    result = FifoReplayResult()
    open_lots_by_symbol: dict[str, list[Lot]] = {}

    timeline: list[tuple[date, int, int, str, object]] = []
    seq = 0
    for adj in opening_adjustments or []:
        timeline.append((adj.opening_date, 0, seq, "OPENING", adj))
        seq += 1
    for ca in corporate_actions or []:
        timeline.append((ca.event_date, 1, seq, "CORPORATE_ACTION", ca))
        seq += 1
    for event in events:
        timeline.append((event.trade_date, 2, seq, "TRADE", event))
        seq += 1
    timeline.sort(key=lambda item: (item[0], item[1], item[2]))

    for _, _, _, kind, payload in timeline:
        if kind == "OPENING":
            _apply_opening_adjustment(payload, result, open_lots_by_symbol)  # type: ignore[arg-type]
            continue
        if kind == "CORPORATE_ACTION":
            _apply_corporate_action(payload, result, open_lots_by_symbol)  # type: ignore[arg-type]
            continue
        _apply_trade(payload, result, open_lots_by_symbol)  # type: ignore[arg-type]

    return result


def _apply_opening_adjustment(
    adj: OpeningPositionAdjustment,
    result: FifoReplayResult,
    open_lots_by_symbol: dict[str, list[Lot]],
) -> None:
    lot = Lot(
        symbol=adj.symbol,
        isin=adj.isin,
        company_id=adj.company_id,
        opened_date=adj.opening_date,
        quantity_opened=adj.quantity,
        quantity_remaining=adj.quantity,
        cost_price=adj.cost_price,
        opening_trade_dedupe_key=f"OPENING_ADJUSTMENT:{adj.symbol}:{adj.opening_date}",
        is_synthetic=True,
        lot_origin="OPENING_ADJUSTMENT",
    )
    open_lots_by_symbol.setdefault(adj.symbol, []).append(lot)
    result.all_lots.append(lot)
    result.warnings.append(
        f"OPENING_POSITION_ADJUSTMENT_APPLIED: {adj.symbol} opened with qty={adj.quantity} "
        f"cost_price={adj.cost_price} on {adj.opening_date} (source={adj.source}, "
        f"confidence={adj.confidence}, reason={adj.reason}) -- the true acquisition trade is "
        "missing from the tradebook; this is a reconciliation-derived estimate, not a real trade."
    )


def _apply_corporate_action(
    ca: CorporateActionEvent,
    result: FifoReplayResult,
    open_lots_by_symbol: dict[str, list[Lot]],
) -> None:
    open_lots = open_lots_by_symbol.get(ca.symbol, [])
    if not open_lots:
        return  # no open position yet at event_date -- nothing to adjust

    old_quantity = sum((lot.quantity_remaining for lot in open_lots), Decimal("0"))
    if old_quantity == 0:
        return
    old_cost_value = sum((lot.quantity_remaining * lot.cost_price for lot in open_lots), Decimal("0"))
    old_cost_per_share = old_cost_value / old_quantity
    factor = ca.ratio_new / ca.ratio_old

    for lot in open_lots:
        lot.quantity_opened *= factor
        lot.quantity_remaining *= factor
        lot.cost_price = lot.cost_price / factor

    result.corporate_action_adjustments.append(
        CorporateActionAdjustmentApplied(
            symbol=ca.symbol,
            event_type=ca.event_type,
            event_date=ca.event_date,
            ratio_new=ca.ratio_new,
            ratio_old=ca.ratio_old,
            old_quantity=old_quantity,
            new_quantity=old_quantity * factor,
            old_cost_per_share=old_cost_per_share,
            adjusted_cost_per_share=old_cost_per_share / factor,
            source=ca.source,
        )
    )


def _apply_trade(
    event: TradeEvent,
    result: FifoReplayResult,
    open_lots_by_symbol: dict[str, list[Lot]],
) -> None:
    if event.side == "BUY":
        lot = Lot(
            symbol=event.symbol,
            isin=event.isin,
            company_id=event.company_id,
            opened_date=event.trade_date,
            quantity_opened=event.quantity,
            quantity_remaining=event.quantity,
            cost_price=event.price,
            opening_trade_dedupe_key=event.dedupe_key,
        )
        open_lots_by_symbol.setdefault(event.symbol, []).append(lot)
        result.all_lots.append(lot)
        return

    # SELL
    remaining_to_sell = event.quantity
    open_lots = open_lots_by_symbol.setdefault(event.symbol, [])
    realized_delta = Decimal("0")

    while remaining_to_sell > 0 and open_lots:
        lot = open_lots[0]
        matched_qty = min(lot.quantity_remaining, remaining_to_sell)
        lot_realized = matched_qty * (event.price - lot.cost_price)
        lot.realized_pnl += lot_realized
        realized_delta += lot_realized
        lot.quantity_remaining -= matched_qty
        remaining_to_sell -= matched_qty
        if lot.quantity_remaining == 0:
            lot.closed_date = event.trade_date
            open_lots.pop(0)

    if remaining_to_sell > 0:
        # Oversell -- likely an untracked corporate action or missing BUY.
        # Absorb the excess as a zero-cost synthetic lot so downstream
        # quantity and realized P&L stay internally consistent, and flag
        # it loudly.
        synthetic_realized = remaining_to_sell * event.price
        synthetic = Lot(
            symbol=event.symbol,
            isin=event.isin,
            company_id=event.company_id,
            opened_date=event.trade_date,
            quantity_opened=remaining_to_sell,
            quantity_remaining=Decimal("0"),
            cost_price=Decimal("0"),
            opening_trade_dedupe_key=event.dedupe_key,
            closed_date=event.trade_date,
            realized_pnl=synthetic_realized,
            is_synthetic=True,
            lot_origin="OVERSELL_SYNTHETIC",
        )
        result.all_lots.append(synthetic)
        realized_delta += synthetic_realized
        result.warnings.append(
            f"SYNTHETIC_LOT_CORPORATE_ACTION_SUSPECTED: {event.symbol} sell of "
            f"{event.quantity} on {event.trade_date} exceeded open lot quantity by "
            f"{remaining_to_sell} -- likely an untracked bonus/split/demerger not "
            "captured in the tradebook, or a missing BUY trade. Excess absorbed as a "
            "zero-cost lot; record a corporate action or opening-position adjustment to resolve."
        )
        remaining_to_sell = Decimal("0")

    result.realized_pnl_by_symbol[event.symbol] = (
        result.realized_pnl_by_symbol.get(event.symbol, Decimal("0")) + realized_delta
    )
