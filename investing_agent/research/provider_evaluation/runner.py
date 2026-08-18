from __future__ import annotations

"""Standalone bake-off runner. Not wired into the main app or CLI.

Usage:
    python -m investing_agent.research.provider_evaluation.runner

Fetches corporate actions + quarterly results for BEL and HAL from every
live-testable adapter, prints a comparison, and writes sanitized evidence
files (request metadata + response body) to ./evidence/.
"""

import asyncio
from pathlib import Path

from investing_agent.research.provider_evaluation.bse_source import BSEEvaluationSource
from investing_agent.research.provider_evaluation.nse_source import NSEEvaluationSource

SYMBOLS = ["BEL", "HAL"]
EVIDENCE_DIR = Path(__file__).parent / "evidence"


async def main() -> None:
    nse = NSEEvaluationSource(evidence_dir=EVIDENCE_DIR)
    bse = BSEEvaluationSource(evidence_dir=EVIDENCE_DIR)

    try:
        for symbol in SYMBOLS:
            print(f"\n{'=' * 60}\n{symbol}\n{'=' * 60}")

            actions = await nse.get_corporate_actions(symbol)
            print(f"\n[NSE] corporate actions: {len(actions)} rows")
            for a in actions[:3]:
                print(f"  {a.action_type:10s} ex={a.ex_date} rec={a.record_date} "
                      f"pay={a.payment_date} amt={a.amount_text!r}")

            results = await nse.get_quarterly_results(symbol)
            print(f"\n[NSE] quarterly results: {len(results)} rows")
            for r in results[:2]:
                print(f"  period={r.period_label} pat={r.pat} eps={r.eps_basic} "
                      f"filed_at={r.filed_at} extraction={r.extraction_method}")

            bse_actions = await bse.get_corporate_actions(symbol)
            print(f"\n[BSE] corporate actions: {len(bse_actions)} rows "
                  f"(expected 0 — see bse_source.py docstring)")

            # Phase 3B addendum: document discovery. These probe candidate
            # endpoints that are NOT verified working — failure just means
            # an empty list, printed plainly so it's obvious in the output.
            announcements = await nse.get_announcements(symbol)
            print(f"\n[NSE] announcements: {len(announcements)} rows")
            for f in announcements[:5]:
                print(f"  {f.filing_type:20s} date={f.filing_date} "
                      f"url={f.document_url!r} title={f.title!r}")

            annual_reports = await nse.get_annual_reports(symbol)
            print(f"\n[NSE] annual reports: {len(annual_reports)} rows")
            for f in annual_reports[:3]:
                print(f"  date={f.filing_date} url={f.document_url!r} title={f.title!r}")

            presentations = await nse.get_investor_presentations(symbol)
            print(f"\n[NSE] investor presentations (filtered from announcements): "
                  f"{len(presentations)} rows")
            for f in presentations[:3]:
                print(f"  date={f.filing_date} url={f.document_url!r} title={f.title!r}")

            # If any candidate document_url turned up, check whether it
            # actually serves PDF bytes (magic-number check only, no full
            # binary saved as evidence).
            candidate_urls = [
                f.document_url
                for f in (announcements + annual_reports)
                if f.document_url
            ]
            if candidate_urls:
                url = candidate_urls[0]
                is_pdf, size, content_type = await nse.check_pdf_downloadable(url)
                print(f"\n[NSE] PDF check on first candidate URL: {url!r}")
                print(f"  is_pdf={is_pdf} size={size} content_type={content_type!r}")
            else:
                print("\n[NSE] PDF check: no candidate document_url found, skipped")

        print(f"\nEvidence written to {EVIDENCE_DIR}/")
        print(f"NSE requests made: {len(nse.evidence)}")
        print(f"BSE requests made: {len(bse.evidence)}")
    finally:
        await nse.aclose()
        await bse.aclose()


if __name__ == "__main__":
    asyncio.run(main())
