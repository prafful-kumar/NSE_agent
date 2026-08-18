# Phase 3 Data Provider Bake-Off — Findings and Recommendation

Date: 2026-08-17. Test symbols: BEL (Bharat Electronics), HAL (Hindustan Aeronautics).
Harness: `investing_agent/research/provider_evaluation/` (ABCs in `interfaces.py`,
live adapters in `nse_source.py` / `bse_source.py`, runner in `runner.py`,
raw evidence in `evidence/`). No paid subscriptions were purchased; no
undocumented/private endpoints were reverse-engineered beyond issuing plain
GET requests to publicly reachable, unauthenticated URLs and recording what
came back.

Per your instruction mid-brief, the paid Tier-2 bake-off (Global Datafeeds /
FinEdge / Indian API / EODHD) was evaluated from public documentation only —
no live calls, no keys purchased — and this report recommends proceeding
straight to **Phase 3A** (NSE + BSE + Company IR fallback, no paid vendor)
rather than spending more time on a live paid-provider comparison.

---

## 1. Provider comparison matrix

Scores: 0 unavailable · 1 poor · 2 partial · 3 good · 4 excellent. "Live-tested" means this bake-off actually issued a request and recorded the response; everything else is documentation-review only.

| Provider | Financials | Corp Actions | Filings | PIT timestamps | Provenance | Ease | Cost | Live-tested |
|---|---|---|---|---|---|---|---|---|
| NSE (official site, HTML/CSV UI + circulars) | 3 | 3 | 3 | 2 | 4 | 2 | free | partial |
| NSE (undocumented JSON API) | 3 | 3 | 0 | 2 | 2 | 3 | free | **yes** |
| BSE (official site, HTML/PDF) | 3 | 3 | 3 | 2 | 4 | 2 | free | partial |
| BSE (undocumented JSON API) | 0 | 0 | 0 | 0 | 0 | 0 | free | **yes (failed)** |
| Company IR (BEL/HAL websites) | 2 | 1 | 3 | 1 | 4 | 3 | free | no |
| Global Datafeeds (authorized vendor) | 4 | 4 | 3 | 3 | 3 | 3 | contact-sales | no |
| FinEdge API | 2 | 2 | 1 | 1 | 1 | 2 | unknown (needs key) | no |
| Indian API (indianapi.in) | 2 | 1 | 0 | 0 | 1 | 3 | unknown/freemium | no |
| EODHD | 2 | 1 | 0 | 1 | 1 | 3 | $60-100/mo | no |
| Tijori Finance | 3 (derived) | 2 | 0 | 0 | 2 | 0 (no public API) | unknown | no |
| Screener.in | 2 (derived) | 1 | 0 | 0 | 1 | 1 (CSV export only, no API) | free | no |

Qualitative notes:

- **NSE undocumented JSON API** — `www.nseindia.com/api/corporates-corporateActions` and `.../api/results-comparision` returned real, correct data for both BEL and HAL to a single plain HTTPS GET with no login, no cookies, no session bootstrap. See §2. This is the same backend NSE's own website widgets call — not a documented product, not versioned, and every response carries Akamai Bot Manager tracking cookies (`_abck`, `bm_sz`), meaning NSE's infra can start challenging automated clients at any time without notice. Scored Ease=3 because it worked *today* with a one-line curl; scored Provenance=2 (not 4) because the JSON gives no traceable link back to a specific filing PDF/XBRL document — it's a convenience projection of the underlying filing, not the filing itself.
- **BSE undocumented JSON API** — `api.bseindia.com/BseIndiaAPI/api/CorpactCurrent` redirected to an error page even with a `Referer` header set. Did not attempt further parameter guessing — that would cross into reverse-engineering a private contract, out of scope. BSE's officially rendered pages (`bseindia.com/corporates/ann`) return HTTP 200 but are JS-rendered SPAs; a plain GET returns an empty shell, not data, so no live BSE JSON data could be captured within this bake-off's constraints.
- **NSE/BSE official site (HTML/CSV/circulars)** — Not live-tested (would require driving a browser or session-authenticated multi-step flow that borders on the anti-bot-defeat prohibition), but its existence and structure is well documented: NSE's Corporate Filings → Financial Results page supports CSV download and XBRL-to-Excel conversion; both exchanges require XBRL filing for financial results and Reg. 30 disclosures per SEBI LODR circulars. This is the actual Tier-1 archival path recommended for Phase 3A (see §6).
- **Global Datafeeds** is an NSE/BSE-**authorized** data vendor (listed on NSE's own authorized-vendor page), offering FIX/WebSocket/REST access to real-time and historical corporate announcements, corporate actions, and financial results. This is the only Tier-2 candidate that is contractually sanctioned rather than scraped-adjacent. Pricing is "contact sales" — not evaluated live per your instruction to skip the paid bake-off.
- **FinEdge / Indian API / EODHD** — public docs reviewed only. None publish a clear India-specific consolidated-vs-standalone flag or a dividend-record/ex/payment-date breakdown in their public docs; EODHD's fundamentals schema is a 70-exchange generic schema not India-tailored. Marked "unverified" rather than scored confidently low, since no live test was run (per your explicit instruction to skip this leg for cost reasons).
- **Tijori** has no self-serve public API (confirmed via their ToS + a Kite Connect forum thread speculating about their data sourcing) — enterprise-only by direct contact. Consistent with your instruction to leave `CompanyResearchSource` unimplemented for now.
- **Screener.in** has no API, only a per-screen CSV export behind a logged-in session; scraping it programmatically at any volume would violate the spirit of their ToS and the "no scraping" constraint. Not viable even for future enrichment without an explicit partnership.

---

## 2. BEL / HAL live sample comparison

Live NSE JSON, 2026-08-17 (raw evidence in `evidence/`):

**BEL dividends (most recent 3 of 20 rows returned):**
| Type | Ex-date | Record date | Payment date | Amount |
|---|---|---|---|---|
| Dividend | 13-Aug-2026 | 13-Aug-2026 | *not published* | Re 0.55/share |
| Interim Dividend | 06-Mar-2026 | 06-Mar-2026 | *not published* | Rs 1.95/share |
| Dividend | 14-Aug-2025 | 14-Aug-2025 | *not published* | Rs 0.90/share |

**HAL dividends (most recent 3 of 20 rows):**
| Type | Ex-date | Record date | Payment date | Amount |
|---|---|---|---|---|
| Dividend | 14-Aug-2026 | 14-Aug-2026 | *not published* | Rs 10/share |
| Interim Dividend | 18-Feb-2026 | 18-Feb-2026 | *not published* | Rs 35/share |
| Dividend | 21-Aug-2025 | 21-Aug-2025 | *not published* | Rs 15/share |

Observation: **NSE never populates a payment date** on this endpoint (field is absent from the schema entirely, not just null-sometimes). Any payment-date field in our domain model will stay empty until we ingest the actual board-resolution PDF — expected and consistent with the "never infer payment_date" rule already encoded in `CorporateAction`.

**BEL Q3 FY24-25 (Oct-Dec 2024) quarterly result — discrepancy example:**

```
BEL Q3 FY24-25 (period 01-OCT-2024..31-DEC-2024)

NSE JSON field `re_net_sale`:      575,612 (₹ lakh) = ₹5,756.12 Cr
NSE JSON field `re_total_inc`:     596,120 (₹ lakh) = ₹5,961.20 Cr  (includes ₹205.08 Cr other income)
NSE JSON field `re_con_pro_loss`:  131,606 (₹ lakh) = ₹1,316.06 Cr
NSE JSON field `re_create_dt`:     30-JAN-2025  (filing/creation timestamp)

Independently verified against BEL's own press release, quoted via
psuconnect.in/bajajbroking.in coverage of the following quarter's results:
"...Rs. 7,121.98 Cr, growth of 23.73% over Rs. 5,756.12 Cr in the
corresponding period of the previous year" (standalone revenue from ops)
"...PAT of Rs. 1,590.06 Cr, growth of 20.82% over Rs. 1,316.06 Cr in the
corresponding period" (standalone PAT)

Match: re_net_sale (5,756.12 Cr) == reported STANDALONE revenue from ops.
Match: re_con_pro_loss (1,316.06 Cr) == reported STANDALONE PAT.

Flag: the field name `re_con_pro_loss` reads as "consolidated profit/loss"
but its value is actually BEL's STANDALONE PAT. For the same quarter a year
later, BEL's own release shows standalone PAT (₹1,590.06 Cr) and
consolidated PAT (₹1,579.70 Cr) differ by ~₹10 Cr — small but real. A naive
implementer trusting the field name would silently mislabel standalone data
as consolidated. Not silently resolved here — flagged for the Phase 3A
adapter to hard-code "this endpoint is standalone-only, ignore the field
name" rather than trust the schema.
```

This is exactly the kind of trap the point-in-time/backtesting system must not inherit silently — see §3.

HAL quarterly results and dividends were retrieved live (`evidence/nse_*_HAL_*.json`) but not independently cross-checked against a third source within this bake-off's time-box; flagged as a follow-up, not asserted as verified.

---

## 3. Point-in-time / backtesting suitability analysis

This is the deciding factor, not raw coverage.

| Requirement | NSE undocumented JSON | NSE/BSE archived filing (PDF/XBRL) | Global Datafeeds (per docs) |
|---|---|---|---|
| `published_at` / source timestamp | Partial — `re_create_dt` on results endpoint looks like a filing-processing timestamp, not necessarily the SEBI-mandated disclosure timestamp; corporate-actions endpoint's `caBroadcastDate` was `null` on every live row tested | Yes — filing PDFs carry an exchange time-stamp (dissemination time) on the document itself | Claimed "real-time & historical" — unverified live |
| `available_at` (when *we* saw it) | Trivial — set at ingestion time in our system | Trivial — same | Trivial — same |
| Historical revisions preserved | **No** — endpoint returns only the current value; no version history, no way to ask "what did this look like before the restatement" | **Yes in principle** — each dated filing is a distinct immutable document; a restatement files a new PDF, doesn't touch the old one | Unverified — docs don't state whether corrections are versioned or overwritten |
| "As known at date X" queryable | **No** | **Yes** — because we archive the raw document and index it by its own filing date, not by our re-fetch time | Unverified |
| Corrections overwrite vs. append | N/A (no history exposed) | Depends on us: **we control this** by archiving every distinct filing/document, never deleting | Unverified |

**Conclusion:** the NSE JSON API is a convenience surface with a *live current snapshot* — it is not, by itself, a valid point-in-time source, because it exposes no revision history and its one timestamp field (`re_create_dt`) is not documented as the authoritative disclosure time. The only sound way to get point-in-time correctness is to **archive every filing document as it's published**, keyed by the exchange's own filing/dissemination date, and treat the JSON API purely as a fast-path hint that gets verified against the archived document before being marked `FACT`. This is already what the `ProvenanceMixin` (`published_at` vs `available_at`) and the `is_latest`/`supersedes_id` versioning on `CorporateAction`/`FinancialResult` were designed for in the models.py work done before this pause — no schema redesign needed, but see §9 for one addition.

---

## 4. Cost / access summary

| Provider | Cost | Access friction |
|---|---|---|
| NSE (official HTML/CSV/circulars) | Free | Session/UI-driven, no API key, but multi-step |
| NSE (undocumented JSON) | Free | None today; can be revoked/challenged anytime |
| BSE (official HTML/PDF) | Free | Session/UI-driven |
| BSE (undocumented JSON) | Free | **Blocked** — not usable as tested |
| Company IR sites | Free | Per-company page format varies |
| Global Datafeeds | Contact sales (enterprise) | Requires commercial agreement; NSE/BSE-authorized |
| FinEdge | Unknown, requires signup | API key required, docs partly JS-gated |
| Indian API | Unknown/freemium | Sandbox available, no key needed to view docs |
| EODHD | $59.99-99.99/mo for fundamentals tier | Self-serve signup |
| Tijori | Enterprise/contact | No self-serve API |
| Screener.in | Free (web), no API | CSV export only, login required |

Given the "skip paid bake-off for now, because of API pricing" instruction, **Phase 3A spends $0** and relies entirely on free official sources, accepting the reliability tradeoffs documented in §5.

---

## 5. Known reliability / maintenance risks

1. **NSE JSON API can be rate-limited or bot-challenged without notice.** Mitigation already built into the harness: self-imposed rate limit (1.5s/request), evidence logging of every request's HTTP status so a production ingester can detect degradation immediately rather than silently getting empty/wrong data.
2. **BSE has no working free structured path today.** Phase 3A's BSE role must be reduced to document-archive + cross-check via officially published announcement/PDF pages, not a JSON adapter — see §7.
3. **Field-name traps** (e.g. `re_con_pro_loss` meaning standalone, not consolidated — §2) mean the NSE adapter needs hard-coded, tested assumptions per field, not generic schema trust. Any future NSE schema change is a silent-corruption risk unless we validate against the archived source document periodically.
4. **No API versioning or deprecation notice from NSE/BSE** for the undocumented endpoints — a production ingester needs to alert on unexpected HTTP status or empty-but-200 responses, not just outright failures.
5. **Company IR page formats are not standardized** across companies — the fallback adapter will need per-company or heuristic-based scraping of investor-relations pages, which is higher maintenance burden per new portfolio company added.
6. **XBRL parsing is deferred** even though "prefer XBRL over PDF" is the stated preference — no XBRL files were actually parsed in this bake-off (only the JSON convenience layer, which is XBRL-tag-named but not raw XBRL). Real XBRL documents live behind NEAPS/Listing Centre filing flows not exercised here. Phase 3A must budget real effort for an XBRL parser against actual `.xml`/`.xbrl` filing attachments, not assume the JSON API is "good enough" XBRL.

---

## 6. Recommended architecture: Pattern C, implemented incrementally

Your hypothesis (Pattern C) is directionally correct as the **long-term target**, but the live evidence changes what Phase 3A can actually build today:

```
Target (Pattern C, eventual):
  NSE/BSE archived filings  →  ground truth (Tier-1 FACT)
  Paid structured API (Global Datafeeds, deferred)  →  convenience/normalized financials
  Tijori (deferred)  →  research enrichment only

Phase 3A (now, $0 budget):
  NSE/BSE archived filing documents  →  ground truth (Tier-1 FACT), fetched via
      official HTML/CSV/circular-linked PDF/XBRL pages
  NSE undocumented JSON API  →  low-volume, rate-limited FAST-PATH HINT ONLY,
      never trusted until cross-checked against the archived document
  BSE  →  archive + cross-check role only (no working JSON path)
  Company IR  →  fallback for filings not found on NSE/BSE (rare, but keeps
      "never overwrite/never miss" promise when an exchange page changes)
```

The reconciliation step from your original Pattern B ("NSE/BSE authoritative + structured provider → reconciliation") still happens in Phase 3A, just between the archived document and the JSON fast-path hint, instead of between two paid vendors. That reconciliation is what upgrades a hint from `data_category=ESTIMATE`-ish convenience data to `FACT`.

---

## 7. Proposed adapter mapping

```
NSEDataSource
  -> FilingSource               fetch corporate-filings HTML page + linked PDF/XBRL
                                  documents; store raw bytes before normalizing
  -> FinancialResultSource      results-comparision JSON as fast-path hint,
                                  cross-checked against the matching filing document
                                  before being marked FACT; is_consolidated is NOT
                                  trusted from the JSON — hard-coded standalone
                                  assumption per §2, verified against the document
  -> CorporateActionSource      corporate-actions JSON as fast-path hint, same
                                  cross-check rule; payment_date left null always
                                  (never populated by this source)

BSEDataSource
  -> FilingSource               announcement/PDF listing pages only (document
                                  fetch, not the blocked JSON API)
  -> CorporateActionSource      cross-check role only — compares NSE-derived
                                  action dates against BSE's independently filed
                                  announcement for the same event; does not
                                  originate data on its own
  -> FinancialResultSource      NOT IMPLEMENTED in Phase 3A (no viable free path
                                  found; revisit if/when a paid vendor is added)

CompanyIRSource (fallback)
  -> FilingSource only          used only when NSE/BSE lack a filing (rare)

TijoriDataSource (future, not implemented)
  -> CompanyResearchSource only  interface left ready per your instruction
```

`CompanyResearchSource` remains fully unimplemented — no adapter registered — consistent with "leave the interface ready but do not implement Tijori yet."

---

## 8. Recommended Phase 3A scope

1. **NSE adapter** — two parts: (a) document fetcher for corporate-filings/circular-linked PDFs and XBRL attachments (the real Tier-1 archive), (b) the already-built `NSEEvaluationSource`-style JSON fast-path for corporate actions + quarterly results, promoted from bake-off harness to a real production adapter with the rate limiting and evidence logging patterns already proven here.
2. **BSE adapter** — document fetcher only (announcements/filing PDFs), used for cross-check, not as a primary financial-results source.
3. **Company IR fallback** — minimal, invoked only when NSE/BSE lack a filing.
4. **Raw document storage** — persist every fetched PDF/XBRL/JSON payload unchanged with `source`, `source_url`, `published_at`, `available_at`, `ingested_at`, `content_hash` before any normalization, per your standing instruction. The JSON fast-path payloads should be tagged distinctly (e.g. `source_type="nse_json_hint"`) from real archived documents (`source_type="nse_filing_pdf"` / `"nse_filing_xbrl"`), so a backtest can choose to trust only document-verified facts.
5. **XBRL parser** — real, not deferred: parse the raw `.xml`/`.xbrl` attachments where available; fall back to the JSON hint (flagged as unverified) only when no XBRL attachment exists.
6. **Corporate actions** — normalize from the cross-checked/verified NSE+BSE data into `CorporateAction`, versioned as already modeled.
7. **Quarterly results** — normalize into `FinancialPeriod`/`FinancialResult`, with `is_consolidated` set from document inspection, never trusted from the JSON field name (§2).
8. **Result/dividend calendar** — the `GET /calendar/dividends` and `GET /calendar/results` endpoints can be built directly on top of `CorporateAction`/`FinancialPeriod` once populated; no new data-source work required for this piece.

---

## 9. Architectural changes needed before migrations

The `ProvenanceMixin`/`CorporateAction`/`FinancialResult`/`Filing`/`Document`/`DocumentVersion` schema drafted in `models.py` before this pause does **not** need a redesign. One addition is worth making before writing migration 003:

- Add a `verified_against_document: bool` (default `False`) column to `CorporateAction` and `FinancialResult`, flipped to `True` only once the ingestion service has matched the JSON-hint-derived row against an actually-archived `Document` (by filing date + company + action/period type) and reconciled the values. This operationalizes §3's conclusion directly in the schema, so `data_category=FACT` can be reserved for `verified_against_document=True` rows, and un-reconciled JSON hints can be stored as a lower-confidence row (`confidence` field, already planned) without blocking ingestion on synchronous document verification.

No other schema changes are needed; the raw-then-normalize ordering, idempotent content-hash dedup, and versioning/supersession design already anticipated this outcome.

---

## 10. Implementation checklist for Phase 3A

- [ ] Add `verified_against_document` column to `CorporateAction`/`FinancialResult` (see §9), then finalize migration 003
- [ ] Promote `NSEEvaluationSource` corporate-actions + quarterly-results logic into a production `NSEDataSource` implementing `FilingSource`/`FinancialResultSource`/`CorporateActionSource` from `investing_agent/services/sources/` (real interfaces, not the bake-off's standalone copies)
- [ ] Build NSE document fetcher (corporate-filings page → linked PDF/XBRL) and real XBRL parser
- [ ] Build `BSEDataSource` as a document-fetcher + cross-check adapter only (no financial-results claim)
- [ ] Build minimal `CompanyIRSource` fallback for filings missing on both exchanges
- [ ] Wire raw `Document` persistence before any normalization, tagging JSON-hint vs archived-document provenance distinctly
- [ ] Implement the NSE↔BSE cross-check + document-verification step that sets `verified_against_document`
- [ ] Build `MockCompanyDataSource` test adapter and fixtures from the sanitized evidence already captured in `evidence/`
- [ ] Repositories for `financial_periods`, `financial_results`, `corporate_actions`, `filings`, `documents`, `document_versions`, `company_events`
- [ ] Ingestion services A-E per the original Phase 3 build order (company resolution → corporate actions/dividends → results calendar → financial results → filings/document archive)
- [ ] `GET /calendar/dividends?days=30`, `GET /calendar/results` endpoints
- [ ] Tests: idempotent re-ingestion, versioning/supersession, amended-filing handling, no-future-data-leakage, NSE/BSE cross-check mismatch handling
- [ ] Keep `CompanyResearchSource` interface defined and unimplemented (no `TijoriDataSource` yet)

Stopping here per your instruction — no further Phase 3 implementation until you've reviewed this recommendation.

---

## 11. Phase 3B addendum — document discovery bake-off (2026-08-18)

Phase 3A is validated (191/191 tests, tagged `phase-3a-validated`). Before building Phase 3B (investor presentations, annual reports, concall transcripts, order book/guidance/segment/operational/capacity facts), the same bake-off discipline from §1-§7 applies: don't guess at document-discovery endpoints, probe them first.

This addendum extends `NSEEvaluationSource` (`nse_source.py`) and `runner.py` with four new methods, none of them live-verified as of this writing (unlike `get_corporate_actions`/`get_quarterly_results`, which §1 already confirmed working):

- `get_announcements(symbol)` — probes `api/corporate-announcements?index=equities&symbol={symbol}`, the same JSON feed NSE's own announcements widget calls. If it returns real rows, each is classified into `filing_type` (`investor_presentation` / `annual_report` / `concall_transcript` / `quarterly_result` / `announcement`) by a best-effort keyword match on the description/subject text (`_classify_announcement`) — never guessed beyond what the text actually supports.
- `get_annual_reports(symbol)` — probes `api/corp-info?symbol={symbol}&corpType=annualreport&market=equities`.
- `get_investor_presentations(symbol)` — no distinct NSE endpoint is known for this; it just filters `get_announcements()` output for the `investor_presentation` classification.
- `check_pdf_downloadable(document_url)` — fetches a candidate `attchmntFile` URL and checks the first 4 bytes for the `%PDF` magic number, without saving the full binary as evidence (keeps the repo small). Reports `(is_pdf, size_bytes, content_type)`.

**Why this matters for Phase 3B's design**: if `document_url` values from these endpoints turn out to be real, directly downloadable PDFs, `services/sources/nse_source.py`'s `FilingSource` implementation (currently unimplemented in production) can be built directly on top of this JSON feed, same as §1's corporate-actions/quarterly-results fast path. If not — non-200 responses, HTML/challenge pages instead of JSON, or `attchmntFile` links that redirect through a JS-gated viewer instead of serving raw bytes — Phase 3B still works end-to-end via the CLI's `archive-document` command (manual archiving of an already-downloaded PDF); live NSE/BSE document discovery becomes a documented open item, not a blocker.

I have no network access in this sandbox, so this bake-off extension is not run by me. Run it locally with:

```
python -m investing_agent.research.provider_evaluation.runner
```

and report back what printed under `[NSE] announcements`, `[NSE] annual reports`, `[NSE] investor presentations`, and the PDF check line. Placeholders below, to fill in after that run:

| Endpoint | Live-tested | Rows returned (BEL) | `document_url` present? | `check_pdf_downloadable` result |
|---|---|---|---|---|
| `api/corporate-announcements` | _pending_ | _pending_ | _pending_ | _pending_ |
| `api/corp-info?corpType=annualreport` | _pending_ | _pending_ | _pending_ | _pending_ |

**Conclusion**: _pending user run._ Do not treat this section as validated until the table above is filled in from real output.
