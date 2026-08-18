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

        print(f"\nEvidence written to {EVIDENCE_DIR}/")
        print(f"NSE requests made: {len(nse.evidence)}")
        print(f"BSE requests made: {len(bse.evidence)}")
    finally:
        await nse.aclose()
        await bse.aclose()


if __name__ == "__main__":
    asyncio.run(main())
