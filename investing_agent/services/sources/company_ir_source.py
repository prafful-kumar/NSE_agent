from __future__ import annotations

"""CompanyIRSource — production adapter, Phase 3A.

Fallback FilingSource used only when NSE/BSE lack a filing. NOT live-tested
in this implementation: fetching and parsing each company's own
investor-relations page reliably requires per-company markup handling that
wasn't verified against real BEL/HAL IR pages within this build. Per
"report unresolved source issues instead of guessing," this adapter is
honest about that gap — it returns an empty list rather than a scraper that
was never validated. company_ir_urls is deliberately configuration, not
scraping logic, so it can be filled in per-company as each is verified.
"""

from investing_agent.services.sources.interfaces import DiscoveredDocument, FilingSource

# Known IR homepages for current portfolio companies — starting point only,
# not fetched by this adapter yet.
KNOWN_IR_URLS: dict[str, str] = {
    "BEL": "https://bel-india.in/investor-relation/",
    "HAL": "https://hal-india.co.in/InvestorRelation",
}


class CompanyIRSource(FilingSource):
    async def get_announcements(self, symbol: str) -> list[DiscoveredDocument]:
        return []

    async def get_filings(self, symbol: str) -> list[DiscoveredDocument]:
        return []

    async def get_annual_reports(self, symbol: str) -> list[DiscoveredDocument]:
        return []

    async def get_investor_presentations(self, symbol: str) -> list[DiscoveredDocument]:
        return []
