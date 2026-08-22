"""PIT-safe, descriptive historical PE-band evidence (historical-pe-band-v1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import CorporateAction, DailyPrice, FinancialPeriod, FinancialResult, ValuationMultipleInput
from investing_agent.services.valuation import MODEL_VERSION

RULE_VERSION = "historical-pe-band-v1"
MIN_OBSERVATIONS = 5


@dataclass(frozen=True)
class HistoricalPEBandOutcome:
    symbol: str
    multiple_input: ValuationMultipleInput | None
    observation_count: int
    excluded: tuple[str, ...]


def _percentile(values: list[Decimal], numerator: int, denominator: int) -> Decimal:
    """Nearest-rank percentile, deliberately fixed and recorded in provenance."""
    ordered = sorted(values)
    index = max(0, (len(ordered) * numerator + denominator - 1) // denominator - 1)
    return ordered[index].quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _ratio_factor(ratio: str | None) -> Decimal | None:
    if not ratio or ":" not in ratio:
        return None
    old, new = ratio.split(":", 1)
    try:
        old_n, new_n = Decimal(old.strip()), Decimal(new.strip())
    except Exception:
        return None
    return new_n / old_n if old_n > 0 and new_n > 0 else None


async def generate_for_company(session: AsyncSession, *, company, as_of: datetime) -> HistoricalPEBandOutcome:
    """Build a PE range from price-date PIT TTM EPS; never looks ahead."""
    as_of = as_of.astimezone(UTC)
    prices = list((await session.execute(
        select(DailyPrice).where(DailyPrice.company_id == company.id, DailyPrice.trading_date <= as_of.date()).order_by(DailyPrice.trading_date)
    )).scalars())
    results = list((await session.execute(
        select(FinancialResult, FinancialPeriod)
        .join(FinancialPeriod, FinancialResult.period_id == FinancialPeriod.id)
        .where(FinancialResult.company_id == company.id, FinancialResult.verification_status == "verified", FinancialResult.reporting_basis == "QUARTER")
    )).all())
    actions = list((await session.execute(
        select(CorporateAction).where(CorporateAction.company_id == company.id, CorporateAction.is_latest.is_(True), CorporateAction.action_type.in_(("split", "bonus")), CorporateAction.available_at <= as_of)
    )).scalars())
    observations: list[dict[str, str]] = []
    exclusions: list[str] = []
    for price in prices:
        price_at = datetime.combine(price.trading_date, datetime.max.time(), tzinfo=UTC)
        # Highest available version per period, then most recently reported four periods.
        latest: dict[object, tuple[FinancialResult, FinancialPeriod]] = {}
        for result, period in results:
            if result.available_at <= price_at:
                current = latest.get(period.id)
                if current is None or result.version > current[0].version:
                    latest[period.id] = (result, period)
        quarters = sorted(latest.values(), key=lambda item: item[1].period_end, reverse=True)[:4]
        if len(quarters) != 4:
            exclusions.append(f"{price.trading_date}: fewer_than_four_available_quarters")
            continue
        eps_values: list[Decimal] = []
        valid = True
        for result, period in quarters:
            eps = result.eps_diluted if result.eps_diluted is not None else result.eps_basic
            if eps is None or eps <= 0:
                valid = False
                break
            factor = Decimal("1")
            for action in actions:
                if action.event_date and period.period_end < action.event_date <= price.trading_date and action.available_at <= price_at:
                    parsed = _ratio_factor(action.ratio)
                    if parsed is None:
                        valid = False
                        break
                    factor *= parsed
            if not valid:
                break
            eps_values.append(Decimal(str(eps)) / factor)
        if not valid:
            exclusions.append(f"{price.trading_date}: EPS_or_share_adjustment_missing")
            continue
        ttm = sum(eps_values)
        if ttm <= 0:
            exclusions.append(f"{price.trading_date}: non_positive_ttm_eps")
            continue
        observations.append({"date": price.trading_date.isoformat(), "pe": str((Decimal(str(price.close)) / ttm).quantize(Decimal("0.0001"))), "ttm_eps": str(ttm), "price_id": str(price.id), "financial_result_ids": ",".join(str(result.id) for result, _ in quarters)})
    if len(observations) < MIN_OBSERVATIONS:
        return HistoricalPEBandOutcome(company.symbol, None, len(observations), tuple(exclusions))
    values = [Decimal(item["pe"]) for item in observations]
    existing = (await session.execute(
        select(ValuationMultipleInput).where(ValuationMultipleInput.company_id == company.id, ValuationMultipleInput.model_version == MODEL_VERSION, ValuationMultipleInput.effective_at == as_of, ValuationMultipleInput.provenance["rule_version"].as_string() == RULE_VERSION)
    )).scalar_one_or_none()
    if existing:
        return HistoricalPEBandOutcome(company.symbol, existing, len(observations), tuple(exclusions))
    row = ValuationMultipleInput(
        company_id=company.id, model_version=MODEL_VERSION, earnings_horizon="TTM", effective_at=as_of, available_at=as_of,
        pe_low=_percentile(values, 25, 100), pe_mid=_percentile(values, 50, 100), pe_high=_percentile(values, 75, 100),
        rationale="PIT historical PE distribution; P25/median/P75 under historical-pe-band-v1.",
        provenance={"rule_version": RULE_VERSION, "earnings_horizon": "TTM", "percentiles": ["P25", "P50", "P75"], "observation_count": len(observations), "observations": observations, "excluded": exclusions},
    )
    session.add(row)
    await session.flush()
    return HistoricalPEBandOutcome(company.symbol, row, len(observations), tuple(exclusions))
