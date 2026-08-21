from __future__ import annotations

"""Unit tests for the pure FIFO lot-matching engine (no DB)."""

from datetime import date
from decimal import Decimal

from investing_agent.services.reconstruction.fifo import (
    CorporateActionEvent,
    OpeningPositionAdjustment,
    TradeEvent,
    replay_fifo,
)


def _event(symbol, side, quantity, price, trade_date, dedupe_key) -> TradeEvent:
    return TradeEvent(
        dedupe_key=dedupe_key,
        symbol=symbol,
        isin=None,
        company_id=None,
        trade_date=trade_date,
        trade_datetime=None,
        side=side,
        quantity=Decimal(str(quantity)),
        price=Decimal(str(price)),
    )


class TestSimpleFifoMatching:
    def test_buy_then_full_sell_realizes_pnl(self) -> None:
        events = [
            _event("INFY", "BUY", 10, 100, date(2024, 1, 1), "T1"),
            _event("INFY", "SELL", 10, 150, date(2024, 2, 1), "T2"),
        ]
        result = replay_fifo(events)

        assert result.realized_pnl_by_symbol["INFY"] == Decimal("500")  # 10 * (150-100)
        assert result.open_lots("INFY") == []
        assert not result.warnings

    def test_partial_sell_leaves_remaining_open_lot(self) -> None:
        events = [
            _event("INFY", "BUY", 10, 100, date(2024, 1, 1), "T1"),
            _event("INFY", "SELL", 4, 150, date(2024, 2, 1), "T2"),
        ]
        result = replay_fifo(events)

        assert result.realized_pnl_by_symbol["INFY"] == Decimal("200")  # 4 * 50
        open_lots = result.open_lots("INFY")
        assert len(open_lots) == 1
        assert open_lots[0].quantity_remaining == Decimal("6")
        assert open_lots[0].cost_price == Decimal("100")


class TestFifoAcrossMultipleLots:
    def test_sell_consumes_oldest_lot_first(self) -> None:
        events = [
            _event("INFY", "BUY", 5, 100, date(2024, 1, 1), "T1"),
            _event("INFY", "BUY", 5, 200, date(2024, 1, 15), "T2"),
            _event("INFY", "SELL", 7, 300, date(2024, 2, 1), "T3"),
        ]
        result = replay_fifo(events)

        # 5 @ 100 fully consumed (realized 5*200=1000) + 2 @ 200 consumed (realized 2*100=200)
        assert result.realized_pnl_by_symbol["INFY"] == Decimal("1200")
        open_lots = result.open_lots("INFY")
        assert len(open_lots) == 1
        assert open_lots[0].quantity_remaining == Decimal("3")
        assert open_lots[0].cost_price == Decimal("200")

    def test_average_cost_blends_across_remaining_open_lots(self) -> None:
        events = [
            _event("INFY", "BUY", 10, 100, date(2024, 1, 1), "T1"),
            _event("INFY", "BUY", 10, 200, date(2024, 1, 15), "T2"),
        ]
        result = replay_fifo(events)
        open_lots = result.open_lots("INFY")
        total_qty = sum((lot.quantity_remaining for lot in open_lots), Decimal("0"))
        total_cost = sum((lot.quantity_remaining * lot.cost_price for lot in open_lots), Decimal("0"))
        assert total_qty == Decimal("20")
        assert total_cost / total_qty == Decimal("150")  # (10*100 + 10*200) / 20


class TestOversell:
    def test_oversell_creates_synthetic_lot_and_warning(self) -> None:
        events = [
            _event("XYZ", "BUY", 10, 100, date(2024, 1, 1), "T1"),
            _event("XYZ", "SELL", 30, 50, date(2024, 2, 1), "T2"),  # 20 more than owned
        ]
        result = replay_fifo(events)

        assert len(result.warnings) == 1
        assert "SYNTHETIC_LOT_CORPORATE_ACTION_SUSPECTED" in result.warnings[0]
        assert "XYZ" in result.warnings[0]

        synthetic_lots = [lot for lot in result.all_lots if lot.is_synthetic]
        assert len(synthetic_lots) == 1
        assert synthetic_lots[0].quantity_opened == Decimal("20")
        assert synthetic_lots[0].cost_price == Decimal("0")

        # realized = 10 real lot sold at (50-100)=-500 + 20 synthetic at 50*20=1000
        assert result.realized_pnl_by_symbol["XYZ"] == Decimal("500")
        assert result.open_lots("XYZ") == []


class TestMultipleSymbolsInterleaved:
    def test_symbols_are_independent(self) -> None:
        events = [
            _event("A", "BUY", 10, 100, date(2024, 1, 1), "A1"),
            _event("B", "BUY", 5, 50, date(2024, 1, 2), "B1"),
            _event("A", "SELL", 10, 120, date(2024, 1, 10), "A2"),
            _event("B", "SELL", 5, 40, date(2024, 1, 11), "B2"),
        ]
        result = replay_fifo(events)

        assert result.realized_pnl_by_symbol["A"] == Decimal("200")
        assert result.realized_pnl_by_symbol["B"] == Decimal("-50")
        assert result.open_lots() == []
        assert set(result.symbols_with_open_lots()) == set()

    def test_realized_pnl_total_sums_across_symbols(self) -> None:
        events = [
            _event("A", "BUY", 10, 100, date(2024, 1, 1), "A1"),
            _event("A", "SELL", 10, 120, date(2024, 1, 10), "A2"),
            _event("B", "BUY", 5, 50, date(2024, 1, 2), "B1"),
            _event("B", "SELL", 5, 40, date(2024, 1, 11), "B2"),
        ]
        result = replay_fifo(events)
        assert result.realized_pnl_total() == Decimal("150")  # 200 + (-50)


class TestCorporateActionAdjustment:
    def test_split_scales_open_lot_quantity_and_cost(self) -> None:
        events = [_event("CDSL", "BUY", 20, 1102.10, date(2022, 5, 16), "T1")]
        actions = [
            CorporateActionEvent(
                symbol="CDSL", event_type="BONUS", event_date=date(2024, 8, 24),
                ratio_new=Decimal("2"), ratio_old=Decimal("1"), source="test",
            )
        ]
        result = replay_fifo(events, corporate_actions=actions)

        open_lots = result.open_lots("CDSL")
        assert len(open_lots) == 1
        assert open_lots[0].quantity_remaining == Decimal("40")
        assert open_lots[0].cost_price == Decimal("551.05")

    def test_adjustment_audit_trail_preserved(self) -> None:
        events = [_event("CAMS", "BUY", 5, 3000, date(2021, 10, 1), "T1")]
        actions = [
            CorporateActionEvent(
                symbol="CAMS", event_type="SPLIT", event_date=date(2025, 12, 5),
                ratio_new=Decimal("5"), ratio_old=Decimal("1"), source="NSE record date",
            )
        ]
        result = replay_fifo(events, corporate_actions=actions)

        assert len(result.corporate_action_adjustments) == 1
        adj = result.corporate_action_adjustments[0]
        assert adj.old_quantity == Decimal("5")
        assert adj.new_quantity == Decimal("25")
        assert adj.old_cost_per_share == Decimal("3000")
        assert adj.adjusted_cost_per_share == Decimal("600")
        assert adj.source == "NSE record date"

    def test_no_open_position_at_event_date_is_a_noop(self) -> None:
        events = [_event("X", "BUY", 10, 100, date(2024, 6, 1), "T1")]
        actions = [
            CorporateActionEvent(
                symbol="X", event_type="SPLIT", event_date=date(2024, 1, 1),
                ratio_new=Decimal("2"), ratio_old=Decimal("1"), source="test",
            )
        ]
        result = replay_fifo(events, corporate_actions=actions)
        assert result.open_lots("X")[0].quantity_remaining == Decimal("10")
        assert not result.corporate_action_adjustments

    def test_split_then_sell_uses_adjusted_quantity(self) -> None:
        events = [
            _event("X", "BUY", 10, 100, date(2024, 1, 1), "T1"),
            _event("X", "SELL", 20, 60, date(2024, 6, 1), "T2"),
        ]
        actions = [
            CorporateActionEvent(
                symbol="X", event_type="SPLIT", event_date=date(2024, 3, 1),
                ratio_new=Decimal("2"), ratio_old=Decimal("1"), source="test",
            )
        ]
        result = replay_fifo(events, corporate_actions=actions)
        # 10 shares @ 100 split into 20 @ 50; sell of 20 @ 60 realizes 20*(60-50)=200
        assert result.realized_pnl_by_symbol["X"] == Decimal("200")
        assert not result.warnings  # no oversell -- the split resolved it


class TestOpeningPositionAdjustment:
    def test_resolves_sell_with_no_prior_buy(self) -> None:
        events = [_event("ZAGGLE", "SELL", 40, 201.80, date(2026, 8, 13), "T1")]
        adjustments = [
            OpeningPositionAdjustment(
                symbol="ZAGGLE", isin=None, company_id=None,
                opening_date=date(2026, 8, 13), quantity=Decimal("40"),
                cost_price=Decimal("458.00"), source="ZERODHA_PNL_RECONCILIATION",
                confidence="MEDIUM", reason="MISSING_TRADE_HISTORY",
            )
        ]
        result = replay_fifo(events, opening_adjustments=adjustments)

        assert not result.warnings or all(
            "SYNTHETIC_LOT_CORPORATE_ACTION_SUSPECTED" not in w for w in result.warnings
        )
        assert any("OPENING_POSITION_ADJUSTMENT_APPLIED" in w for w in result.warnings)
        assert result.realized_pnl_by_symbol["ZAGGLE"] == Decimal("-10248.00")
        assert result.open_lots("ZAGGLE") == []
