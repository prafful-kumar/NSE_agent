from __future__ import annotations

"""Unit tests for services/dedup/news_dedup.py — exact duplicate detection
(compute_content_hash), near-duplicate detection, and event clustering.

All models are constructed in-memory (no DB) since these functions only
read attributes off NewsItem/NewsEvent instances.
"""

from datetime import UTC, datetime, timedelta

from investing_agent.db.models import NewsEvent, NewsItem
from investing_agent.services.dedup.news_dedup import (
    compute_content_hash,
    find_best_cluster,
    find_near_duplicate,
    is_near_duplicate,
    score_event_candidate,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _news_item(headline: str, published_at: datetime | None = NOW) -> NewsItem:
    return NewsItem(
        headline=headline,
        feed_description=None,
        publisher="Test Publisher",
        source_name="livemint",
        source_url=f"https://example.com/{hash(headline)}",
        published_at=published_at,
        content_hash=compute_content_hash("livemint", headline),
    )


def _news_event(
    representative_headline: str,
    last_seen_at: datetime = NOW,
) -> NewsEvent:
    return NewsEvent(
        event_type="unclassified",
        first_seen_at=last_seen_at,
        last_seen_at=last_seen_at,
        representative_headline=representative_headline,
    )


class TestComputeContentHash:
    def test_exact_duplicate_same_hash(self) -> None:
        h1 = compute_content_hash("livemint", "BEL wins order worth Rs 500 crore")
        h2 = compute_content_hash("livemint", "BEL wins order worth Rs 500 crore")
        assert h1 == h2

    def test_case_and_punctuation_insensitive(self) -> None:
        h1 = compute_content_hash("livemint", "BEL wins order worth Rs 500 crore!")
        h2 = compute_content_hash("livemint", "bel WINS order, worth Rs 500 crore")
        assert h1 == h2

    def test_different_source_different_hash(self) -> None:
        h1 = compute_content_hash("livemint", "BEL wins order worth Rs 500 crore")
        h2 = compute_content_hash("economic_times", "BEL wins order worth Rs 500 crore")
        assert h1 != h2

    def test_different_headline_different_hash(self) -> None:
        h1 = compute_content_hash("livemint", "BEL wins order worth Rs 500 crore")
        h2 = compute_content_hash("livemint", "HAL wins order worth Rs 200 crore")
        assert h1 != h2


class TestNearDuplicate:
    def test_near_duplicate_minor_wording_change(self) -> None:
        assert is_near_duplicate(
            "BEL wins Rs 500 crore order from Indian Navy",
            "BEL wins Rs 500 crore order from the Indian Navy",
        )

    def test_not_near_duplicate_different_story(self) -> None:
        assert not is_near_duplicate(
            "BEL wins Rs 500 crore order from Indian Navy",
            "HAL delivers Tejas aircraft to Indian Air Force",
        )

    def test_find_near_duplicate_returns_match(self) -> None:
        existing = [
            _news_item("HAL delivers Tejas aircraft to Indian Air Force"),
            _news_item("BEL wins Rs 500 crore order from the Indian Navy"),
        ]
        match = find_near_duplicate("BEL wins Rs 500 crore order from Indian Navy", existing)
        assert match is not None
        assert match.headline == "BEL wins Rs 500 crore order from the Indian Navy"

    def test_find_near_duplicate_returns_none_when_no_match(self) -> None:
        existing = [_news_item("HAL delivers Tejas aircraft to Indian Air Force")]
        match = find_near_duplicate("BEL wins Rs 500 crore order from Indian Navy", existing)
        assert match is None

    def test_find_near_duplicate_empty_candidates(self) -> None:
        assert find_near_duplicate("Any headline", []) is None


class TestScoreEventCandidate:
    def test_similar_headline_within_window_scores(self) -> None:
        item = _news_item("BEL bags Rs 500 crore order from Indian Navy", published_at=NOW)
        event = _news_event(
            "BEL wins Rs 500 crore order from the Indian Navy", last_seen_at=NOW
        )
        score = score_event_candidate(item, event)
        assert score is not None
        assert score >= 0.55

    def test_outside_time_window_rejected(self) -> None:
        item = _news_item(
            "BEL bags Rs 500 crore order from Indian Navy",
            published_at=NOW,
        )
        far_past = NOW - timedelta(hours=72)
        event = _news_event("BEL bags Rs 500 crore order from Indian Navy", last_seen_at=far_past)
        assert score_event_candidate(item, event) is None

    def test_dissimilar_headline_rejected(self) -> None:
        item = _news_item("BEL bags Rs 500 crore order from Indian Navy", published_at=NOW)
        event = _news_event("HAL delivers Tejas aircraft to Indian Air Force", last_seen_at=NOW)
        assert score_event_candidate(item, event) is None

    def test_conflicting_numeric_terms_rejected(self) -> None:
        item = _news_item("BEL bags Rs 500 crore order from Indian Navy", published_at=NOW)
        event = _news_event("BEL bags Rs 900 crore order from Indian Navy", last_seen_at=NOW)
        assert score_event_candidate(item, event) is None

    def test_no_published_at_skips_time_gate(self) -> None:
        item = _news_item("BEL bags Rs 500 crore order from Indian Navy", published_at=None)
        far_past = NOW - timedelta(hours=72)
        event = _news_event(
            "BEL bags Rs 500 crore order from Indian Navy", last_seen_at=far_past
        )
        assert score_event_candidate(item, event) is not None


class TestFindBestCluster:
    def test_picks_highest_scoring_candidate(self) -> None:
        item = _news_item("BEL bags Rs 500 crore order from Indian Navy", published_at=NOW)
        weak = _news_event("BEL announces new order from Indian Navy", last_seen_at=NOW)
        strong = _news_event(
            "BEL bags Rs 500 crore order from the Indian Navy", last_seen_at=NOW
        )
        best = find_best_cluster(item, [weak, strong])
        assert best is strong

    def test_returns_none_when_no_candidates_match(self) -> None:
        item = _news_item("BEL bags Rs 500 crore order from Indian Navy", published_at=NOW)
        event = _news_event("HAL delivers Tejas aircraft to Indian Air Force", last_seen_at=NOW)
        assert find_best_cluster(item, [event]) is None

    def test_empty_candidates_returns_none(self) -> None:
        item = _news_item("BEL bags Rs 500 crore order from Indian Navy", published_at=NOW)
        assert find_best_cluster(item, []) is None
