from __future__ import annotations

"""Phase 4 news bake-off — NOT wired into the app. Scope per explicit user
instruction: evaluate free RSS sources only (Google News, LiveMint,
Economic Times, Moneycontrol-if-it-works), report coverage/dedup/licensing,
and STOP. Do not scrape article pages or bypass paywalls — RSS only.

Usage:
    python3 investing_agent/research/provider_evaluation/news_bakeoff.py
"""

import difflib
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

UTC = timezone.utc
EVIDENCE_DIR = Path(__file__).parent / "evidence"
UA = "Mozilla/5.0 (compatible; investing-agent-research/1.0)"

SYMBOLS = {
    "BEL": "Bharat Electronics",
    "HAL": "Hindustan Aeronautics",
}


@dataclass
class NewsItem:
    source_feed: str          # which RSS feed this came from
    title: str
    link: str
    description: str
    pub_date_raw: str | None
    pub_date: datetime | None
    publisher_name: str | None   # <source> tag (Google News only)
    publisher_url: str | None


@dataclass
class FeedResult:
    feed_name: str
    url: str
    http_status: int | None
    error: str | None
    raw_item_count: int
    items: list[NewsItem] = field(default_factory=list)


def _fetch(url: str) -> tuple[int | None, str | None, bytes | None]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, None, resp.read()
    except Exception as exc:  # noqa: BLE001 — bake-off harness, report any failure
        return None, repr(exc), None


def _parse_pubdate(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _parse_rss(feed_name: str, url: str, body: bytes) -> list[NewsItem]:
    items: list[NewsItem] = []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return items
    ns = {"media": "http://search.yahoo.com/mrss/"}
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        pub_raw = item.findtext("pubDate")
        source_el = item.find("source")
        pub_name = source_el.text.strip() if source_el is not None and source_el.text else None
        pub_url = source_el.get("url") if source_el is not None else None
        items.append(
            NewsItem(
                source_feed=feed_name,
                title=title,
                link=link,
                description=description,
                pub_date_raw=pub_raw,
                pub_date=_parse_pubdate(pub_raw),
                publisher_name=pub_name,
                publisher_url=pub_url,
            )
        )
    return items


def fetch_feed(feed_name: str, url: str) -> FeedResult:
    status, err, body = _fetch(url)
    if EVIDENCE_DIR and body:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        safe_name = re.sub(r"[^a-zA-Z0-9_]+", "_", feed_name)
        (EVIDENCE_DIR / f"news_{safe_name}_{stamp}.xml").write_bytes(body[:200_000])
    if body is None:
        return FeedResult(feed_name, url, status, err, 0, [])
    items = _parse_rss(feed_name, url, body)
    return FeedResult(feed_name, url, status, err, len(items), items)


def _mentions(text: str, symbol: str, company_name: str) -> bool:
    """Whole-word match on symbol OR company name (case-insensitive).

    Deliberately conservative — a bare substring match on a 3-letter symbol
    like BEL would false-positive on unrelated words. This is the same
    relevance bar a real ingestion filter would need.
    """
    text_l = text.lower()
    if re.search(rf"\b{re.escape(symbol.lower())}\b", text_l):
        return True
    if company_name.lower() in text_l:
        return True
    # First word of company name (e.g. "Bharat", "Hindustan") is too broad
    # to count alone — require the full name or the exact symbol.
    return False


def google_news_query(query: str, when: str) -> FeedResult:
    url = (
        f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}+when:{when}"
        f"&hl=en-IN&gl=IN&ceid=IN:en"
    )
    return fetch_feed(f"google_news[{query}|{when}]", url)


def _publisher_key(item: NewsItem) -> str:
    """Identity of the underlying outlet, not the RSS feed we polled it
    from — a single Google News search window already aggregates many
    different publishers, which is where the real dedup problem shows up.
    """
    return (item.publisher_name or item.source_feed).lower()


def dedup_report(all_items: list[NewsItem]) -> list[tuple[NewsItem, NewsItem, float]]:
    """Pairwise title-similarity across DIFFERENT publishers only (two items
    from the same outlet are just that outlet's own feed, not a dedup case).
    difflib ratio is a crude but dependency-free proxy for 'same underlying
    story, different outlet'.
    """
    pairs = []
    for i, a in enumerate(all_items):
        for b in all_items[i + 1 :]:
            if _publisher_key(a) == _publisher_key(b):
                continue
            ratio = difflib.SequenceMatcher(None, a.title.lower(), b.title.lower()).ratio()
            if ratio >= 0.5:
                pairs.append((a, b, ratio))
    return pairs


def main() -> None:
    for symbol, company_name in SYMBOLS.items():
        print(f"\n{'=' * 70}\n{symbol} ({company_name})\n{'=' * 70}")

        # ── Google News RSS: symbol query, three windows ────────────────────
        gnews_results: dict[str, FeedResult] = {}
        for when in ("1d", "7d", "30d"):
            fr = google_news_query(symbol, when)
            gnews_results[when] = fr
            relevant = [it for it in fr.items if _mentions(it.title + " " + it.description, symbol, company_name)]
            irrelevant = [it for it in fr.items if it not in relevant]
            print(
                f"[google_news q={symbol!r} when:{when}] status={fr.http_status} "
                f"raw={fr.raw_item_count} relevant={len(relevant)} irrelevant={len(irrelevant)}"
            )
            for it in fr.items[:5]:
                mark = "OK " if _mentions(it.title + " " + it.description, symbol, company_name) else "IRR"
                print(f"    [{mark}] {it.pub_date_raw!r:38s} {it.publisher_name!r:20s} {it.title[:70]}")
            if when == "1d" and irrelevant:
                print(f"    -- sample irrelevant hits (noise on bare symbol query) --")
                for it in irrelevant[:5]:
                    print(f"    [IRR] {it.publisher_name!r:20s} {it.title[:70]}")

        # Company-name query for comparison (does full name reduce noise?)
        fr_name = google_news_query(company_name, "30d")
        relevant_name = [it for it in fr_name.items if _mentions(it.title + " " + it.description, symbol, company_name)]
        print(
            f"[google_news q={company_name!r} when:30d] status={fr_name.http_status} "
            f"raw={fr_name.raw_item_count} relevant={len(relevant_name)}"
        )

        # ── LiveMint companies feed (general, filter client-side) ───────────
        lm = fetch_feed("livemint_companies", "https://www.livemint.com/rss/companies")
        lm_matches = [it for it in lm.items if _mentions(it.title + " " + it.description, symbol, company_name)]
        print(f"[livemint_companies general feed] status={lm.http_status} raw={lm.raw_item_count} matches={len(lm_matches)}")
        for it in lm_matches[:5]:
            print(f"    {it.pub_date_raw!r:38s} {it.title[:70]}")

        # ── Economic Times markets feed (general, filter client-side) ───────
        et = fetch_feed(
            "et_markets_stocks",
            "https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms",
        )
        et_matches = [it for it in et.items if _mentions(it.title + " " + it.description, symbol, company_name)]
        print(f"[et_markets_stocks general feed] status={et.http_status} raw={et.raw_item_count} matches={len(et_matches)}")
        for it in et_matches[:5]:
            print(f"    {it.pub_date_raw!r:38s} {it.title[:70]}")

        # ── Moneycontrol: freshness check only ───────────────────────────────
        mc = fetch_feed("moneycontrol_latestnews", "https://www.moneycontrol.com/rss/latestnews.xml")
        newest = max((it.pub_date for it in mc.items if it.pub_date), default=None)
        age_days = (datetime.now(UTC) - newest).days if newest else None
        print(f"[moneycontrol_latestnews] status={mc.http_status} raw={mc.raw_item_count} newest_item_age_days={age_days}")

        # ── Cross-publisher dedup within this symbol's relevant items ───────
        pool = [it for it in gnews_results["30d"].items if _mentions(it.title + " " + it.description, symbol, company_name)]
        pool += lm_matches + et_matches
        publishers = {_publisher_key(it) for it in pool}
        print(f"[coverage] {len(pool)} relevant items from {len(publishers)} distinct publishers (30d)")
        dupes = dedup_report(pool)
        print(f"[dedup] candidate pairs across DIFFERENT publishers (title similarity >= 0.5): {len(dupes)}")
        for a, b, ratio in dupes[:6]:
            print(f"    {ratio:.2f}  [{a.publisher_name or a.source_feed}] {a.title[:45]!r}  ~=  [{b.publisher_name or b.source_feed}] {b.title[:45]!r}")

        time.sleep(1)  # be polite between symbols


if __name__ == "__main__":
    main()
