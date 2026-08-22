from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from click.testing import CliRunner

from investing_agent.cli import cli
from investing_agent.services.current_recommendations import _evidence_snapshot


DECISION_ID = "11111111-1111-1111-1111-111111111111"


def test_review_cli_requires_a_human_supplied_verdict(monkeypatch) -> None:
    recorder = AsyncMock()
    monkeypatch.setattr("investing_agent.cli._recommendation_review", recorder)

    result = CliRunner().invoke(cli, ["recommendation-review", "--decision-id", DECISION_ID])

    assert result.exit_code != 0
    recorder.assert_not_awaited()


def test_review_cli_records_only_the_explicit_verdict(monkeypatch) -> None:
    recorder = AsyncMock()
    monkeypatch.setattr("investing_agent.cli._recommendation_review", recorder)

    result = CliRunner().invoke(
        cli,
        [
            "recommendation-review",
            "--decision-id",
            DECISION_ID,
            "--verdict",
            "DISAGREE",
            "--reason",
            "Reviewer supplied rationale",
        ],
    )

    assert result.exit_code == 0
    recorder.assert_awaited_once_with(DECISION_ID, "DISAGREE", "Reviewer supplied rationale")


def test_review_report_cli_is_read_only(monkeypatch) -> None:
    reporter = AsyncMock()
    monkeypatch.setattr("investing_agent.cli._recommendation_review_report", reporter)

    result = CliRunner().invoke(cli, ["recommendation-review-report", "--symbol", "cdsl"])

    assert result.exit_code == 0
    reporter.assert_awaited_once_with(None, "CDSL")


def test_new_recommendation_evidence_snapshot_preserves_earnings_horizon() -> None:
    estimate = SimpleNamespace(
        model_version="deterministic-v1",
        cutoff_at=datetime(2026, 8, 21, tzinfo=UTC),
        earnings_horizon="NEXT_QUARTER",
        eps_low=Decimal("1.10"),
        eps_base=Decimal("1.25"),
        eps_high=Decimal("1.40"),
        confidence=Decimal("0.70"),
        feature_snapshot_id="snapshot-1",
    )

    snapshot = _evidence_snapshot("estimate", estimate)

    assert snapshot == {
        "model_version": "deterministic-v1",
        "cutoff_at": "2026-08-21T00:00:00+00:00",
        "earnings_horizon": "NEXT_QUARTER",
        "eps_low": "1.10",
        "eps_mid": "1.25",
        "eps_high": "1.40",
        "confidence": "0.70",
        "feature_snapshot_id": "snapshot-1",
    }
