"""Frozen deterministic valuation v1 with explicit earnings-horizon safety.

Historical PE bands use PIT TTM EPS. This module therefore values them only
against TTM EPS; a Phase 5A ``NEXT_QUARTER`` EstimateRun is ineligible.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.db.models import DailyPrice, FinancialPeriod, FinancialResult, ValuationMultipleInput, ValuationSnapshot

MODEL_VERSION = "deterministic-valuation-v1"
METHOD = "HISTORICAL_PE_TTM"
MAX_FINANCIAL_AGE = timedelta(days=180)
TTM_HORIZON = "TTM"
_PCT = Decimal("100")
_TTM_CONFIDENCE = Decimal("0.8000")


@dataclass(frozen=True)
class ValuationOutcome:
    symbol: str
    snapshot: ValuationSnapshot | None
    gaps: tuple[str, ...]


@dataclass(frozen=True)
class EarningsSeries:
    """PIT-safe EPS series with one explicit compatible horizon."""
    horizon: str
    low: Decimal
    mid: Decimal
    high: Decimal
    result_ids: tuple[str, ...]
    components: tuple[dict[str, str], ...]
    source_document_id: object | None


def _q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def horizons_compatible(*, earnings_horizon: str, multiple_horizon: str) -> bool:
    """Only apply a PE range to the earnings horizon it describes."""
    return earnings_horizon == multiple_horizon and earnings_horizon in {"TTM", "FORWARD_12M", "FY_FORWARD"}


async def _latest_financial(session: AsyncSession, company_id, as_of: datetime) -> FinancialResult | None:
    row = (await session.execute(
        select(FinancialResult).join(FinancialPeriod, FinancialResult.period_id == FinancialPeriod.id).where(
            FinancialResult.company_id == company_id, FinancialResult.is_latest.is_(True),
            FinancialResult.verification_status == "verified", FinancialResult.available_at <= as_of,
        ).order_by(FinancialPeriod.period_end.desc(), FinancialResult.available_at.desc()).limit(1)
    )).scalar_one_or_none()
    return row if row is not None and as_of - row.available_at <= MAX_FINANCIAL_AGE else None


async def _ttm_earnings(session: AsyncSession, company_id, as_of: datetime, *, scope: str) -> EarningsSeries | None:
    """Latest four contiguous available verified quarterly diluted-EPS facts.

    No quarter is annualised and no estimate is silently mixed into TTM.
    """
    rows = list((await session.execute(
        select(FinancialResult, FinancialPeriod).join(FinancialPeriod, FinancialResult.period_id == FinancialPeriod.id).where(
            FinancialResult.company_id == company_id, FinancialResult.verification_status == "verified",
            FinancialResult.reporting_basis == "QUARTER", FinancialResult.statement_scope == scope,
            FinancialResult.available_at <= as_of,
        )
    )).all())
    latest: dict[object, tuple[FinancialResult, FinancialPeriod]] = {}
    for result, period in rows:
        current = latest.get(period.id)
        if current is None or (result.version, result.available_at) > (current[0].version, current[0].available_at):
            latest[period.id] = (result, period)
    quarters = sorted(latest.values(), key=lambda item: item[1].period_end, reverse=True)[:4]
    if len(quarters) != 4:
        return None
    ordered = list(reversed(quarters))
    quarter_order = ["Q1", "Q2", "Q3", "Q4"]
    positions = [quarter_order.index(period.quarter) if period.quarter in quarter_order else -1 for _, period in ordered]
    if positions[0] < 0 or any(positions[index] != (positions[0] + index) % 4 for index in range(4)):
        return None
    eps_values: list[Decimal] = []
    components: list[dict[str, str]] = []
    for result, period in ordered:
        if result.eps_diluted is None or Decimal(str(result.eps_diluted)) <= 0:
            return None
        eps = Decimal(str(result.eps_diluted))
        eps_values.append(eps)
        components.append({"financial_result_id": str(result.id), "period": period.label, "eps_diluted": str(eps), "available_at": result.available_at.isoformat()})
    total = sum(eps_values)
    return EarningsSeries(TTM_HORIZON, total, total, total, tuple(str(result.id) for result, _ in ordered), tuple(components), ordered[-1][0].source_document_id)


async def generate_for_company(session: AsyncSession, *, company, as_of: datetime) -> ValuationOutcome:
    """Generate an immutable matched-horizon TTM snapshot or explicit gaps."""
    as_of = as_of.astimezone(UTC)
    financial = await _latest_financial(session, company.id, as_of)
    if financial is None:
        return ValuationOutcome(company.symbol, None, ("verified_financial_missing_or_stale",))
    earnings = await _ttm_earnings(session, company.id, as_of, scope=financial.statement_scope)
    if earnings is None:
        return ValuationOutcome(company.symbol, None, ("pit_ttm_eps_missing_or_noncontiguous",))
    multiple = (await session.execute(
        select(ValuationMultipleInput).where(
            ValuationMultipleInput.company_id == company.id, ValuationMultipleInput.model_version == MODEL_VERSION,
            ValuationMultipleInput.effective_at <= as_of, ValuationMultipleInput.available_at <= as_of,
        ).order_by(ValuationMultipleInput.effective_at.desc(), ValuationMultipleInput.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if multiple is None:
        return ValuationOutcome(company.symbol, None, ("auditable_pe_multiple_missing",))
    if not horizons_compatible(earnings_horizon=earnings.horizon, multiple_horizon=multiple.earnings_horizon):
        return ValuationOutcome(company.symbol, None, ("incompatible_eps_and_pe_horizons",))
    if not (multiple.pe_low > 0 and multiple.pe_low <= multiple.pe_mid <= multiple.pe_high):
        return ValuationOutcome(company.symbol, None, ("invalid_pe_multiple_range",))
    price = (await session.execute(
        select(DailyPrice).where(DailyPrice.company_id == company.id, DailyPrice.trading_date <= as_of.date()).order_by(DailyPrice.trading_date.desc()).limit(1)
    )).scalar_one_or_none()
    if price is None or price.close <= 0:
        return ValuationOutcome(company.symbol, None, ("raw_market_price_missing",))
    low, mid, high = _q(earnings.low * multiple.pe_low), _q(earnings.mid * multiple.pe_mid), _q(earnings.high * multiple.pe_high)
    existing = (await session.execute(
        select(ValuationSnapshot).where(ValuationSnapshot.company_id == company.id, ValuationSnapshot.as_of == as_of, ValuationSnapshot.valuation_model_version == MODEL_VERSION).limit(1)
    )).scalar_one_or_none()
    if existing:
        return ValuationOutcome(company.symbol, existing, ())
    snapshot = ValuationSnapshot(
        company_id=company.id, as_of=as_of, valuation_method=METHOD, valuation_model_version=MODEL_VERSION,
        earnings_basis="VERIFIED_TTM_EPS", earnings_horizon=earnings.horizon,
        eps_low=earnings.low, eps_mid=earnings.mid, eps_high=earnings.high,
        pe_low=multiple.pe_low, pe_mid=multiple.pe_mid, pe_high=multiple.pe_high,
        fair_value_low=low, fair_value_mid=mid, fair_value_high=high, current_price=price.close,
        upside_downside_low_pct=_q((low / price.close - 1) * _PCT), upside_downside_pct=_q((mid / price.close - 1) * _PCT), upside_downside_high_pct=_q((high / price.close - 1) * _PCT), confidence=_TTM_CONFIDENCE,
        source_document_id=earnings.source_document_id,
        assumptions={"multiple_input_id": str(multiple.id), "earnings_horizon": earnings.horizon, "multiple_horizon": multiple.earnings_horizon, "ttm_components": list(earnings.components), "price_date": price.trading_date.isoformat()},
        evidence=[*[{"type": "financial_result", "id": result_id} for result_id in earnings.result_ids], {"type": "valuation_multiple_input", "id": str(multiple.id)}, {"type": "daily_price", "id": str(price.id)}],
    )
    session.add(snapshot)
    await session.flush()
    return ValuationOutcome(company.symbol, snapshot, ())
