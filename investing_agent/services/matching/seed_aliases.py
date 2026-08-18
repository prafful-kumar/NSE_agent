from __future__ import annotations

"""Seed data for the deterministic CompanyAlias table.

Only BEL and HAL are seeded for Phase 4A — the two symbols exercised in the
news bake-off. Coverage expands the same way (seed_default_aliases growing,
or a future record-alias CLI command) as more companies get news ingestion.
"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.repositories.company import CompanyRepository
from investing_agent.db.repositories.company_alias import CompanyAliasRepository
from investing_agent.schemas.news import CompanyAliasCreate

# (symbol, alias, alias_type, match_confidence)
DEFAULT_ALIAS_SEEDS: list[tuple[str, str, str, Decimal]] = [
    ("BEL", "Bharat Electronics", "full_name", Decimal("1.00")),
    ("BEL", "Bharat Electronics Ltd", "full_name", Decimal("1.00")),
    ("BEL", "BEL", "symbol", Decimal("0.70")),
    ("HAL", "Hindustan Aeronautics", "full_name", Decimal("1.00")),
    ("HAL", "Hindustan Aeronautics Ltd", "full_name", Decimal("1.00")),
    # Bare "HAL" collides heavily with other tickers/words (bake-off
    # REPORT.md §12) — seeded below CompanyMatcher.AMBIGUOUS_THRESHOLD so it
    # is only ever a strong signal alongside a full-name hit; standalone it
    # is downgraded to match_method="alias_symbol_ambiguous".
    ("HAL", "HAL", "symbol", Decimal("0.20")),
]


async def seed_default_aliases(session: AsyncSession) -> list[str]:
    """Idempotent: skips any (company, alias) pair that already exists.

    Returns symbols for which no matching Company row was found, so the
    caller can surface a warning instead of silently skipping them.
    """
    companies = CompanyRepository(session)
    aliases = CompanyAliasRepository(session)
    missing_symbols: list[str] = []

    for symbol, alias_text, alias_type, confidence in DEFAULT_ALIAS_SEEDS:
        company = await companies.get_by_symbol(symbol)
        if company is None:
            if symbol not in missing_symbols:
                missing_symbols.append(symbol)
            continue
        existing = await aliases.get_by_company_and_alias(company.id, alias_text)
        if existing is not None:
            continue
        await aliases.create(
            CompanyAliasCreate(
                company_id=company.id,
                alias=alias_text,
                alias_type=alias_type,
                match_confidence=confidence,
            )
        )

    return missing_symbols
