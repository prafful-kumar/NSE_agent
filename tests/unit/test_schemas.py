"""Unit tests for Pydantic schemas."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest


class TestCompanySchema:
    def test_valid_nse_company(self):
        from investing_agent.schemas.company import CompanyCreate
        c = CompanyCreate(symbol="BEL", name="Bharat Electronics Ltd", exchange="NSE")
        assert c.symbol == "BEL"
        assert c.exchange == "NSE"

    def test_invalid_exchange(self):
        from investing_agent.schemas.company import CompanyCreate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CompanyCreate(symbol="BEL", name="BEL", exchange="NASDAQ")

    def test_symbol_uppercase(self):
        from investing_agent.schemas.company import CompanyCreate
        c = CompanyCreate(symbol="reliance", name="Reliance Industries", exchange="NSE")
        assert c.symbol == "reliance"  # schema doesn't auto-uppercase; repository does


class TestCorporateEventSchema:
    def test_dividend_event(self):
        from investing_agent.schemas.events import CorporateEventCreate
        e = CorporateEventCreate(
            symbol="INFY",
            event_type="dividend",
            event_date=date(2026, 10, 15),
            amount=Decimal("21.00"),
            source="NSE",
        )
        assert e.event_type == "dividend"
        assert e.amount == Decimal("21.00")
        assert e.payment_date is None  # must not be inferred

    def test_payment_date_not_required(self):
        """payment_date is optional — must never be assumed."""
        from investing_agent.schemas.events import CorporateEventCreate
        e = CorporateEventCreate(
            symbol="TCS",
            event_type="dividend",
            event_date=date(2026, 8, 1),
            source="BSE",
        )
        assert e.payment_date is None

    def test_invalid_event_type(self):
        from investing_agent.schemas.events import CorporateEventCreate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CorporateEventCreate(
                symbol="TCS",
                event_type="fake_event",
                event_date=date(2026, 8, 1),
                source="NSE",
            )


class TestThesisSchema:
    def test_create_thesis(self):
        from investing_agent.schemas.thesis import InvestmentThesisCreate
        t = InvestmentThesisCreate(
            symbol="HAL",
            thesis="Defence duopoly with strong order book growth.",
            status="active",
            buy_reasons=["Order book 5x revenue", "Margin expansion"],
            horizon_months=24,
        )
        assert t.symbol == "HAL"
        assert t.status == "active"

    def test_horizon_months_bounds(self):
        from investing_agent.schemas.thesis import InvestmentThesisCreate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            InvestmentThesisCreate(symbol="HAL", horizon_months=0)
        with pytest.raises(ValidationError):
            InvestmentThesisCreate(symbol="HAL", horizon_months=361)


class TestRecommendationSchema:
    def test_valid_actions(self):
        from investing_agent.schemas.recommendations import RecommendationCreate
        for action in ["BUY", "ADD", "HOLD", "REDUCE", "AVOID", "WATCH", "INSUFFICIENT_EVIDENCE"]:
            r = RecommendationCreate(symbol="BEL", action=action)
            assert r.action == action

    def test_invalid_action(self):
        from investing_agent.schemas.recommendations import RecommendationCreate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RecommendationCreate(symbol="BEL", action="STRONG_BUY")

    def test_confidence_range(self):
        from investing_agent.schemas.recommendations import RecommendationCreate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RecommendationCreate(symbol="BEL", action="HOLD", confidence=Decimal("1.5"))
        with pytest.raises(ValidationError):
            RecommendationCreate(symbol="BEL", action="HOLD", confidence=Decimal("-0.1"))


class TestPortfolioSchema:
    def test_broker_holding(self):
        from investing_agent.schemas.portfolio import BrokerHolding
        h = BrokerHolding(
            tradingsymbol="RELIANCE",
            exchange="NSE",
            quantity=10,
            average_price=Decimal("2450.00"),
            last_price=Decimal("2680.00"),
            pnl=Decimal("2300.00"),
        )
        assert h.tradingsymbol == "RELIANCE"
        assert h.quantity == 10
