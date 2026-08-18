from __future__ import annotations

"""Capability interfaces used by the bake-off harness.

Deliberately four separate ABCs (not one giant CompanyDataSource) so that a
provider which is strong at, say, corporate actions but has no filings
coverage can still be scored and later wired in for just that capability.
Mirrors the Phase 2 PortfolioReader-style adapter pattern.

Raw* DTOs below hold provider output *before* normalization into internal
domain models — the bake-off only measures what a provider can give us and
how trustworthy the timestamps are; it does not write to PostgreSQL.
"""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Evidence:
    """Reproducibility record for a single live provider request.

    Kept separate from the parsed payload so the harness can persist evidence
    even when parsing fails (e.g. blocked/challenge response).
    """

    provider: str
    request_type: str
    symbol: str
    requested_at: datetime
    source_url: str
    http_status: int | None
    response_hash: str | None
    notes: str = ""

    @staticmethod
    def hash_body(body: bytes) -> str:
        return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class RawCorporateAction:
    provider: str
    symbol: str
    isin: str | None
    action_type: str  # dividend|bonus|split|buyback|agm|other
    announced_date: str | None
    ex_date: str | None
    record_date: str | None
    payment_date: str | None
    amount_text: str | None
    published_at: datetime | None  # when the source says this became public
    available_at: datetime  # when *we* retrieved it (harness run time)
    source_url: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawFinancialResult:
    provider: str
    symbol: str
    period_label: str | None
    is_consolidated: bool | None
    revenue: str | None
    pat: str | None
    eps_basic: str | None
    result_date: str | None
    filed_at: datetime | None  # source-reported creation/filing timestamp
    available_at: datetime
    extraction_method: str  # "xbrl" | "structured_api" | "pdf_manual"
    source_url: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawFiling:
    provider: str
    symbol: str
    # quarterly_result|annual_report|investor_presentation|announcement|concall_transcript
    filing_type: str
    title: str
    filing_date: str | None
    document_url: str | None
    available_at: datetime
    source_url: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawResearchMetric:
    provider: str
    symbol: str
    metric_type: str  # segment_revenue|shareholding|operational_metric
    label: str
    value: str | None
    as_of: str | None
    available_at: datetime
    source_url: str
    raw: dict[str, Any] = field(default_factory=dict)


class FilingSource(ABC):
    """Announcements, filings, annual reports, investor presentations."""

    @abstractmethod
    async def get_announcements(self, symbol: str) -> list[RawFiling]: ...

    @abstractmethod
    async def get_filings(self, symbol: str) -> list[RawFiling]: ...

    @abstractmethod
    async def get_annual_reports(self, symbol: str) -> list[RawFiling]: ...

    @abstractmethod
    async def get_investor_presentations(self, symbol: str) -> list[RawFiling]: ...


class FinancialResultSource(ABC):
    """Quarterly / annual reported financial results."""

    @abstractmethod
    async def get_quarterly_results(self, symbol: str) -> list[RawFinancialResult]: ...

    @abstractmethod
    async def get_annual_results(self, symbol: str) -> list[RawFinancialResult]: ...


class CorporateActionSource(ABC):
    """Dividends, bonus, split, buyback, board meetings / result dates."""

    @abstractmethod
    async def get_corporate_actions(self, symbol: str) -> list[RawCorporateAction]: ...

    @abstractmethod
    async def get_dividends(self, symbol: str) -> list[RawCorporateAction]: ...

    @abstractmethod
    async def get_board_meetings(self, symbol: str) -> list[RawCorporateAction]: ...


class CompanyResearchSource(ABC):
    """Tier-2/3 enrichment: operational metrics, segment mix, shareholding.

    No provider in this bake-off implements this yet (Tijori integration is
    explicitly deferred). Kept here so a future TijoriDataSource has a stable
    contract to implement without touching the other three interfaces.
    """

    @abstractmethod
    async def get_operational_metrics(self, symbol: str) -> list[RawResearchMetric]: ...

    @abstractmethod
    async def get_segment_data(self, symbol: str) -> list[RawResearchMetric]: ...

    @abstractmethod
    async def get_shareholding(self, symbol: str) -> list[RawResearchMetric]: ...
