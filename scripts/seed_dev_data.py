"""Seed the development database with representative test data.

Run: python scripts/seed_dev_data.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from investing_agent.config.logging import configure_logging
from investing_agent.db.session import AsyncSessionLocal
from investing_agent.db.models import UserPreference, WatchlistItem
from investing_agent.db.repositories.company import CompanyRepository
from investing_agent.db.repositories.portfolio import PortfolioRepository
from investing_agent.db.repositories.thesis import ThesisRepository
from investing_agent.gateway.mock import MockBrokerGateway
from investing_agent.schemas.company import CompanyCreate
from investing_agent.schemas.thesis import InvestmentThesisCreate

configure_logging()

COMPANIES = [
    CompanyCreate(symbol="RELIANCE", name="Reliance Industries Ltd", exchange="NSE",
                  sector="Energy", industry="Oil & Gas", market_cap_category="Large"),
    CompanyCreate(symbol="HDFCBANK", name="HDFC Bank Ltd", exchange="NSE",
                  sector="Banking", industry="Private Banks", market_cap_category="Large"),
    CompanyCreate(symbol="BEL", name="Bharat Electronics Ltd", exchange="NSE",
                  sector="Defence", industry="Defence Electronics", market_cap_category="Large"),
    CompanyCreate(symbol="HAL", name="Hindustan Aeronautics Ltd", exchange="NSE",
                  sector="Defence", industry="Aerospace", market_cap_category="Large"),
    CompanyCreate(symbol="TCS", name="Tata Consultancy Services", exchange="NSE",
                  sector="IT", industry="Software", market_cap_category="Large"),
    CompanyCreate(symbol="INFY", name="Infosys Ltd", exchange="NSE",
                  sector="IT", industry="Software", market_cap_category="Large"),
]

THESES = [
    InvestmentThesisCreate(
        symbol="BEL",
        status="active",
        thesis=(
            "Bharat Electronics is a defence electronics duopoly in India. "
            "Strong order book (5x revenue) provides multi-year revenue visibility. "
            "Government's defence indigenisation push is a structural tailwind. "
            "Margin expansion expected as product mix improves."
        ),
        buy_reasons=[
            "Order book 5x trailing revenue with multi-year visibility",
            "Defence indigenisation = structural demand growth",
            "Consistent ROCE > 25%, debt-free balance sheet",
            "Dividend yield provides downside cushion",
        ],
        risk_factors=[
            "Order execution delays could compress near-term revenue",
            "Government capex slowdown risk",
            "Competition from private players (L&T Defence, Adani Defence)",
        ],
        catalysts=[
            "Quarterly order inflow > ₹5000 crore",
            "New product categories (EW, radars)",
            "Export order wins",
        ],
        invalidation_conditions=[
            "Order book falls below 3x revenue",
            "EBITDA margin drops below 20% for two consecutive quarters",
            "Major accounting irregularity",
        ],
        target_price_low=180,
        target_price_base=230,
        target_price_high=270,
        horizon_months=18,
    ),
    InvestmentThesisCreate(
        symbol="HAL",
        status="active",
        thesis=(
            "Hindustan Aeronautics is India's primary aerospace OEM. "
            "Massive ₹1.5L+ crore order book from IAF, Navy, Army provides 10+ year visibility. "
            "Tejas Mk1A, LCH, Dhruv upgrade cycles create sustained demand. "
            "Revenue CAGR guidance of 15-18% for next 3 years."
        ),
        buy_reasons=[
            "Monopoly on military aircraft MRO in India",
            "Order book > 10 years of revenue",
            "Rising defence budget allocation",
        ],
        risk_factors=[
            "Execution bottlenecks on Tejas Mk1A",
            "Engine import dependency (GE F404)",
            "Margin pressure from fixed-price contracts",
        ],
        catalysts=[
            "Tejas Mk1A deliveries ramp up",
            "Export orders (Malaysia, Egypt)",
        ],
        invalidation_conditions=[
            "Significant order cancellations",
            "EBITDA margin falls below 18%",
        ],
        target_price_low=3000,
        target_price_base=3800,
        target_price_high=4500,
        horizon_months=24,
    ),
]

WATCHLIST = ["IRFC", "COCHIN", "MAZAGON", "BHEL", "NTPC"]


async def seed() -> None:
    print("Seeding development database...")
    async with AsyncSessionLocal() as session:
        # Companies
        company_repo = CompanyRepository(session)
        for c in COMPANIES:
            company = await company_repo.upsert(c)
            print(f"  Upserted company: {company.symbol}")

        # Portfolio snapshot from mock broker
        broker = MockBrokerGateway()
        response = await broker.get_holdings()
        portfolio_repo = PortfolioRepository(session)
        snapshot = await portfolio_repo.save_from_broker("default", response)
        print(f"  Created portfolio snapshot: {snapshot.id}")

        # User preferences
        from sqlalchemy import select
        existing_pref = await session.execute(
            select(UserPreference).where(UserPreference.user_id == "default")
        )
        if not existing_pref.scalar_one_or_none():
            pref = UserPreference(
                user_id="default",
                preferred_sectors=["Defence", "Banking", "IT"],
                avoided_sectors=["Gambling", "Tobacco"],
                preferred_holding_period_months=18,
                risk_tolerance="medium",
                max_stock_allocation_pct=15,
                max_sector_allocation_pct=40,
            )
            session.add(pref)
            print("  Created user preferences")

        # Investment theses
        thesis_repo = ThesisRepository(session)
        for t in THESES:
            existing = await thesis_repo.get_active("default", t.symbol)
            if not existing:
                thesis = await thesis_repo.create("default", t)
                print(f"  Created thesis: {thesis.symbol}")
            else:
                print(f"  Thesis already exists: {t.symbol}")

        # Watchlist
        for symbol in WATCHLIST:
            existing = await session.execute(
                select(WatchlistItem).where(
                    WatchlistItem.user_id == "default",
                    WatchlistItem.symbol == symbol,
                )
            )
            if not existing.scalar_one_or_none():
                session.add(WatchlistItem(
                    user_id="default",
                    symbol=symbol,
                    reason="Monitoring for entry opportunity",
                    is_active=True,
                ))
                print(f"  Added to watchlist: {symbol}")

        await session.commit()
    print("\nDone! Development database seeded.")


if __name__ == "__main__":
    asyncio.run(seed())
