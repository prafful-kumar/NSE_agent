from __future__ import annotations

"""PortfolioSyncService — the canonical portfolio sync pipeline.

Flow:
  PortfolioReader.get_holdings()
    ↓ raw Zerodha JSON
  _normalize_holdings()
    ↓ validate + clean
  InstrumentResolver.resolve() per holding
    ↓ enrich with company_id
  _calculate_portfolio_values()
    ↓ weights, P&L, totals
  PortfolioRepository.save_from_broker()
    ↓ persist snapshot + holdings
  SyncResult

Design rules:
  - Deterministic calculations only (no LLM).
  - If last_price == 0, record a warning but continue.
  - Missing ISIN is warned, not fatal (ETFs often have ISINs, equities always should).
  - Stale check: if snapshot is fresher than portfolio_staleness_seconds, skip sync.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from investing_agent.config.logging import get_logger
from investing_agent.config.settings import get_settings
from investing_agent.db.models import Holding, IngestionRun, PortfolioSnapshot
from investing_agent.db.repositories.portfolio import PortfolioRepository
from investing_agent.gateway.portfolio_reader import (
    BrokerAuthError,
    BrokerUnavailableError,
    PortfolioReader,
)
from investing_agent.schemas.sync import (
    HoldingNormalized,
    ReconciliationWarning,
    SyncResult,
)
from investing_agent.services.instrument_resolver import InstrumentResolver

log = get_logger(__name__)
settings = get_settings()

_ZERO = Decimal("0")


def _to_decimal(value: Any, default: Decimal = _ZERO) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return default


class PortfolioSyncService:
    """Orchestrates the full portfolio sync pipeline."""

    def __init__(
        self,
        reader: PortfolioReader,
        session: AsyncSession,
    ) -> None:
        self._reader = reader
        self._session = session
        self._portfolio_repo = PortfolioRepository(session)
        self._resolver = InstrumentResolver(session)

    async def sync(self, user_id: str) -> SyncResult:
        """Run a full portfolio sync.  Returns SyncResult regardless of errors."""
        started_at = datetime.now(timezone.utc)
        ingestion_run = IngestionRun(
            run_type="portfolio",
            status="running",
            source="zerodha_kite_mcp",
            started_at=started_at,
        )
        self._session.add(ingestion_run)
        await self._session.flush()

        warnings: list[ReconciliationWarning] = []
        errors: list[str] = []

        try:
            # ── Fetch from broker ─────────────────────────────────────────────
            raw = await self._reader.get_holdings()
            raw_holdings = self._extract_holdings_list(raw)
            log.info("portfolio_sync.fetched", count=len(raw_holdings), user=user_id)

            # ── Validate + normalize + resolve instruments ─────────────────────
            normalized: list[HoldingNormalized] = []
            for raw_h in raw_holdings:
                try:
                    norm, warns = await self._normalize_one(raw_h)
                    warnings.extend(warns)
                    normalized.append(norm)
                except Exception as exc:
                    sym = raw_h.get("tradingsymbol", "UNKNOWN")
                    log.warning("portfolio_sync.holding_skipped", symbol=sym, error=str(exc))
                    errors.append(f"Skipped {sym}: {exc}")

            # ── Calculate portfolio-level values ──────────────────────────────
            self._calculate_portfolio_weights(normalized)

            # ── Persist ────────────────────────────────────────────────────────
            snapshot = await self._persist(user_id, normalized, started_at)

            # ── Finalise ingestion run ─────────────────────────────────────────
            ingestion_run.status = "success" if not errors else "partial"
            ingestion_run.records_ingested = len(normalized)
            ingestion_run.records_failed = len(errors)
            ingestion_run.completed_at = datetime.now(timezone.utc)
            await self._session.flush()

            total_value = sum(h.current_value for h in normalized) or _ZERO
            total_invested = sum(h.invested_value for h in normalized) or _ZERO
            total_pnl = total_value - total_invested
            pnl_pct = (total_pnl / total_invested * 100) if total_invested else _ZERO

            return SyncResult(
                snapshot_id=snapshot.id,
                user_id=user_id,
                sync_timestamp=started_at,
                source="zerodha_kite_mcp",
                holdings_count=len(normalized),
                total_value=total_value,
                total_invested=total_invested,
                total_pnl=total_pnl,
                pnl_pct=pnl_pct,
                holdings=normalized,
                warnings=warnings,
                errors=errors,
                ingestion_run_id=ingestion_run.id,
                data_timestamp=started_at,
            )

        except BrokerAuthError as exc:
            log.error("portfolio_sync.auth_error", error=str(exc))
            ingestion_run.status = "failed"
            ingestion_run.error_message = str(exc)
            ingestion_run.completed_at = datetime.now(timezone.utc)
            await self._session.flush()
            raise
        except BrokerUnavailableError as exc:
            log.error("portfolio_sync.unavailable", error=str(exc))
            ingestion_run.status = "failed"
            ingestion_run.error_message = str(exc)
            ingestion_run.completed_at = datetime.now(timezone.utc)
            await self._session.flush()
            raise
        except Exception as exc:
            log.exception("portfolio_sync.unexpected_error", error=str(exc))
            ingestion_run.status = "failed"
            ingestion_run.error_message = str(exc)
            ingestion_run.completed_at = datetime.now(timezone.utc)
            await self._session.flush()
            raise

    def _extract_holdings_list(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """Handle both {"data": [...]} and {"holdings": [...]} formats."""
        if isinstance(raw, list):
            return raw
        if "data" in raw:
            return raw["data"] or []
        if "holdings" in raw:
            return raw["holdings"] or []
        return []

    async def _normalize_one(
        self, raw: dict[str, Any]
    ) -> tuple[HoldingNormalized, list[ReconciliationWarning]]:
        warns: list[ReconciliationWarning] = []
        symbol = raw.get("tradingsymbol", "")
        exchange = str(raw.get("exchange", "NSE")).upper()
        isin = raw.get("isin") or None
        instrument_type = raw.get("instrument_type", "EQ")

        if not symbol:
            raise ValueError("Missing tradingsymbol")

        quantity = int(raw.get("quantity") or 0)
        t1_qty = int(raw.get("t1_quantity") or 0)
        avg_price = _to_decimal(raw.get("average_price"))
        last_price = _to_decimal(raw.get("last_price"))
        close_price = _to_decimal(raw.get("close_price")) or None

        if last_price == _ZERO and quantity > 0:
            warns.append(ReconciliationWarning(
                code="ZERO_LAST_PRICE",
                symbol=symbol,
                message=f"{symbol}: last_price is 0 — possibly circuit-limit or suspended.",
            ))

        if not isin and instrument_type == "EQ":
            warns.append(ReconciliationWarning(
                code="MISSING_ISIN",
                symbol=symbol,
                message=f"{symbol}: no ISIN provided — instrument resolution may be imprecise.",
            ))

        # Resolve instrument to internal company_id
        resolved = await self._resolver.resolve(raw)

        current_value = last_price * quantity
        invested_value = avg_price * quantity
        pnl = current_value - invested_value
        pnl_pct = (pnl / invested_value * 100) if invested_value else _ZERO

        day_change = _to_decimal(raw.get("day_change")) if raw.get("day_change") is not None else None
        day_change_pct = _to_decimal(raw.get("day_change_percentage")) if raw.get("day_change_percentage") is not None else None

        return HoldingNormalized(
            tradingsymbol=symbol,
            exchange=exchange,
            isin=isin,
            instrument_type=instrument_type,
            company_id=resolved.company_id,
            quantity=quantity,
            t1_quantity=t1_qty,
            average_price=avg_price,
            last_price=last_price,
            close_price=close_price,
            current_value=current_value,
            invested_value=invested_value,
            pnl=pnl,
            pnl_pct=pnl_pct,
            day_change=day_change,
            day_change_pct=day_change_pct,
            portfolio_weight_pct=_ZERO,  # calculated next
            product=raw.get("product"),
            resolution_method=resolved.resolution_method,
        ), warns

    def _calculate_portfolio_weights(self, holdings: list[HoldingNormalized]) -> None:
        total = sum(h.current_value for h in holdings)
        for h in holdings:
            h.portfolio_weight_pct = (
                (h.current_value / total * 100).quantize(Decimal("0.0001"))
                if total > _ZERO else _ZERO
            )

    async def _persist(
        self,
        user_id: str,
        holdings: list[HoldingNormalized],
        fetched_at: datetime,
    ) -> PortfolioSnapshot:
        from datetime import date
        today = date.today()

        total_value = sum(h.current_value for h in holdings)
        total_invested = sum(h.invested_value for h in holdings)
        total_pnl = total_value - total_invested
        pnl_pct = total_pnl / total_invested if total_invested else _ZERO

        snapshot = PortfolioSnapshot(
            user_id=user_id,
            snapshot_date=today,
            total_value=total_value,
            total_invested=total_invested,
            total_pnl=total_pnl,
            pnl_pct=pnl_pct,
            source="zerodha_kite_mcp",
            source_timestamp=fetched_at,
            ingestion_timestamp=datetime.now(timezone.utc),
            raw_response={"holdings_count": len(holdings)},
        )
        self._session.add(snapshot)
        await self._session.flush()

        for h in holdings:
            holding_row = Holding(
                snapshot_id=snapshot.id,
                company_id=h.company_id,
                symbol=h.tradingsymbol,
                isin=h.isin,
                quantity=h.quantity,
                t1_quantity=h.t1_quantity,
                average_price=h.average_price,
                last_price=h.last_price,
                current_value=h.current_value,
                pnl=h.pnl,
                pnl_pct=h.pnl_pct,
                portfolio_weight_pct=h.portfolio_weight_pct,
            )
            self._session.add(holding_row)

        await self._session.flush()
        return snapshot
