from __future__ import annotations

"""Production capability interfaces for Phase 3A company-intelligence sources.

Four separate ABCs, not one giant CompanyDataSource, so a provider strong at
one capability (e.g. corporate actions) can be wired in independently of the
others. Mirrors the Phase 2 PortfolioReader adapter pattern and the earlier
bake-off harness (investing_agent/research/provider_evaluation/interfaces.py)
— this module is the production counterpart; the bake-off copy stays
untouched as a standalone evaluation artifact and is never imported here.

Every discovery method that can back an archived fact returns a
DiscoveredDocument alongside the parsed Raw* rows: the document is what gets
persisted to source_documents (Tier-1 ground truth, byte-for-byte), and the
Raw* rows are what normalization turns into CorporateAction/FinancialResult
candidates. A Raw* row is NEVER persisted as a verified fact without a
DiscoveredDocument backing it — see services/verification.py.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class DiscoveredDocument:
    """Raw bytes fetched from a source, not yet archived to source_documents.

    source_type distinguishes an undocumented JSON convenience endpoint
    (e.g. "nse_json_hint") from a real archived filing
    ("nse_filing_pdf"/"nse_filing_xbrl") — this distinction is what lets
    verification later refuse to mark a JSON-hint-derived fact as verified
    just because the API returned a value.
    """

    company_symbol: str
    exchange: str  # NSE|BSE|IR
    source: str  # NSE|BSE|COMPANY_IR
    source_type: str  # e.g. nse_json_hint | nse_filing_pdf | bse_announcement_pdf
    # quarterly_result|annual_report|investor_presentation|announcement|concall_transcript|other
    filing_type: str
    document_type: str  # pdf|html|xml|xbrl|json|txt
    title: str
    content: bytes
    source_url: str
    fetched_at: datetime
    published_at: datetime | None = None
    mime_type: str | None = None
    # Set only for a document extracted from a parent ZIP archive (see
    # services/extraction/zip_archive.py) — links a child PDF/HTML back to
    # the original archived ZIP it came from. None for everything else.
    parent_document_id: uuid.UUID | None = None


@dataclass(frozen=True)
class RawCorporateAction:
    action_type: str  # dividend|bonus|split|buyback|rights|agm|board_meeting|other
    announced_date: str | None
    event_date: str | None
    ex_date: str | None
    record_date: str | None
    payment_date: str | None
    agm_date: str | None
    amount_text: str | None
    dividend_type: str | None
    board_meeting_announced_at: str | None
    board_meeting_date: str | None
    expected_result_date: str | None
    actual_result_published_at: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawFinancialResult:
    period_start: str | None
    period_end: str | None
    result_date: str | None
    revenue: str | None
    pat: str | None
    pbt: str | None
    eps_basic: str | None
    is_audited_hint: bool | None
    # Adapter-asserted hints; normalization decides the final value and may
    # override with UNRESOLVED if the adapter can't actually prove it.
    statement_scope_hint: str | None  # STANDALONE|CONSOLIDATED|None
    unit_scale_hint: str | None  # LAKH|CRORE|ACTUAL|None
    extraction_method: str  # xbrl|structured_api|pdf_manual|mock
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawFiling:
    filing_type: str
    title: str
    filing_date: str | None
    document_url: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawAnnouncement:
    """One row from a filing/announcement discovery feed — metadata only,
    NOT yet downloaded. A source's discovery methods (get_announcements etc.)
    return these cheaply for every row; download_attachment is a separate,
    explicit call so a caller can decide which categories are worth the
    bandwidth (see FilingIngestionService's auto-archive allowlist) instead
    of every discovery call implying a multi-MB download.
    """

    provider: str
    symbol: str
    # investor_presentation|annual_report|concall_transcript|quarterly_result|
    # order_contract|announcement|other
    filing_type: str
    title: str
    filing_date: str | None  # raw source string, e.g. "13-Aug-2026 17:48:45"
    published_at: datetime | None
    document_url: str | None
    source_url: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DownloadedAttachment:
    """Bytes fetched for a RawAnnouncement.document_url. is_pdf/is_zip are
    decided from magic bytes, never from the URL extension or the server's
    content-type header alone — an anti-bot challenge page can serve
    text/html with a 200 status at a URL that ends in .pdf."""

    content: bytes
    content_type: str | None
    is_pdf: bool
    is_zip: bool


class SourceAccessError(Exception):
    """A source signaled it's blocking automated access (e.g. HTTP 403 from
    an anti-bot layer like Akamai Bot Manager). Never retried automatically —
    retrying a block looks like an attempt to bypass it. Callers should
    surface this and stop for the current sync run, not rotate user agents,
    solve challenges, or fall back to a headless browser."""


class SourceTransientError(Exception):
    """A source failed in a way that's plausibly transient (network error,
    timeout, HTTP 429, HTTP 5xx) — safe to retry with backoff, unlike
    SourceAccessError."""


@dataclass(frozen=True)
class RawResearchMetric:
    metric_type: str  # segment_revenue|shareholding|operational_metric
    label: str
    value: str | None
    as_of: str | None
    raw: dict[str, Any] = field(default_factory=dict)


class FilingSource(ABC):
    """Announcements, filings, annual reports, investor presentations.

    Discovery methods return RawAnnouncement (metadata only — title, date,
    category, document_url); download_attachment is the separate, explicit
    step that fetches bytes for a chosen document_url. This split exists
    because a single symbol's announcement feed can run into the thousands
    of rows — discovery must stay cheap even when only a handful of
    categories are worth auto-downloading (see Phase 3B FilingIngestionService).
    """

    @abstractmethod
    async def get_announcements(self, symbol: str) -> list[RawAnnouncement]: ...

    @abstractmethod
    async def get_filings(self, symbol: str) -> list[RawAnnouncement]: ...

    @abstractmethod
    async def get_annual_reports(self, symbol: str) -> list[RawAnnouncement]: ...

    @abstractmethod
    async def get_investor_presentations(self, symbol: str) -> list[RawAnnouncement]: ...

    @abstractmethod
    async def download_attachment(self, document_url: str) -> DownloadedAttachment | None: ...


class FinancialResultSource(ABC):
    """Quarterly / annual reported financial results."""

    @abstractmethod
    async def get_quarterly_results(
        self, symbol: str
    ) -> tuple[list[RawFinancialResult], DiscoveredDocument | None]: ...

    @abstractmethod
    async def get_annual_results(
        self, symbol: str
    ) -> tuple[list[RawFinancialResult], DiscoveredDocument | None]: ...


class CorporateActionSource(ABC):
    """Dividends, bonus, split, buyback, board meetings / result dates."""

    @abstractmethod
    async def get_corporate_actions(
        self, symbol: str
    ) -> tuple[list[RawCorporateAction], DiscoveredDocument | None]: ...

    @abstractmethod
    async def get_dividends(
        self, symbol: str
    ) -> tuple[list[RawCorporateAction], DiscoveredDocument | None]: ...

    @abstractmethod
    async def get_board_meetings(
        self, symbol: str
    ) -> tuple[list[RawCorporateAction], DiscoveredDocument | None]: ...


class CompanyResearchSource(ABC):
    """Tier-2/3 enrichment: operational metrics, segment mix, shareholding.

    No adapter implements this in Phase 3A (Tijori integration is explicitly
    deferred). Kept so a future TijoriDataSource has a stable contract.
    """

    @abstractmethod
    async def get_operational_metrics(self, symbol: str) -> list[RawResearchMetric]: ...

    @abstractmethod
    async def get_segment_data(self, symbol: str) -> list[RawResearchMetric]: ...

    @abstractmethod
    async def get_shareholding(self, symbol: str) -> list[RawResearchMetric]: ...
