from __future__ import annotations

"""Deterministic near-duplicate detection and event clustering for Phase 4A
news ingestion. No LLM — headline normalization + difflib similarity +
publication-time proximity + numeric-term overlap, same class of technique
validated in the bake-off (research/provider_evaluation/news_bakeoff.py
::dedup_report), promoted here to production with explicit thresholds.

Two distinct jobs, kept in one module because they share the same
normalization/similarity primitives:

1. Near-duplicate NewsItem detection (is_near_duplicate) — catches the same
   publisher re-crawled under a slightly different URL or headline, where
   content_hash (exact match on normalized headline) already missed it.
2. NewsEvent clustering (find_best_cluster) — decides whether a freshly
   ingested NewsItem is evidence for an existing NewsEvent (e.g. an ET
   headline joining a LiveMint headline about the same order win) or should
   start a new one. NewsItem rows are never merged; only NewsEvent
   membership (news_event_items) changes.
"""

import difflib
import hashlib
import re
from datetime import timedelta

from investing_agent.db.models import NewsEvent, NewsItem

NEAR_DUPLICATE_THRESHOLD = 0.90
CLUSTER_HEADLINE_THRESHOLD = 0.55
CLUSTER_TIME_WINDOW = timedelta(hours=48)

_PUNCTUATION_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def normalize_headline(headline: str) -> str:
    text = _PUNCTUATION_RE.sub(" ", headline.lower())
    return _WHITESPACE_RE.sub(" ", text).strip()


def extract_numbers(text: str) -> set[str]:
    """Numeric tokens (order values, percentages, etc.) — used as a
    disambiguating signal since two headlines can be textually similar but
    describe different-sized events."""
    return set(_NUMBER_RE.findall(text))


def compute_content_hash(source_name: str, headline: str) -> str:
    """Dedup key: normalized headline + source, so re-polling the same
    story under a different query-param URL still hashes identically."""
    normalized = normalize_headline(headline)
    digest_input = f"{source_name}|{normalized}"
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()


def headline_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_headline(a), normalize_headline(b)).ratio()


def is_near_duplicate(new_headline: str, existing_headline: str) -> bool:
    return headline_similarity(new_headline, existing_headline) >= NEAR_DUPLICATE_THRESHOLD


def find_near_duplicate(new_headline: str, candidates: list[NewsItem]) -> NewsItem | None:
    """First candidate (same source_name, already filtered by caller) whose
    headline is a near-duplicate of new_headline, or None."""
    for candidate in candidates:
        if is_near_duplicate(new_headline, candidate.headline):
            return candidate
    return None


def score_event_candidate(new_item: NewsItem, candidate_event: NewsEvent) -> float | None:
    """Similarity score in [0, 1] if candidate_event is a plausible cluster
    match for new_item, else None (ruled out by a hard gate: time window,
    headline threshold, or conflicting numeric terms)."""
    if new_item.published_at is not None:
        reference_time = candidate_event.last_seen_at
        if abs(new_item.published_at - reference_time) > CLUSTER_TIME_WINDOW:
            return None

    similarity = headline_similarity(new_item.headline, candidate_event.representative_headline)
    if similarity < CLUSTER_HEADLINE_THRESHOLD:
        return None

    new_numbers = extract_numbers(new_item.headline)
    rep_numbers = extract_numbers(candidate_event.representative_headline)
    if new_numbers and rep_numbers and new_numbers.isdisjoint(rep_numbers):
        # Both headlines cite figures but they disagree — likely a
        # different underlying event (e.g. two different order sizes),
        # even if the surrounding text is similar.
        return None

    return similarity


def find_best_cluster(new_item: NewsItem, candidates: list[NewsEvent]) -> NewsEvent | None:
    best: NewsEvent | None = None
    best_score = 0.0
    for candidate in candidates:
        score = score_event_candidate(new_item, candidate)
        if score is not None and score > best_score:
            best, best_score = candidate, score
    return best
