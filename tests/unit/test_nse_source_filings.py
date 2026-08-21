from __future__ import annotations

"""Unit tests for NSEDataSource's Phase 3B FilingSource methods, plus the
module-level classification/date-parsing helpers.

Network is mocked with httpx.MockTransport (no new dependency, no real
requests) — NSEDataSource accepts an injectable httpx.AsyncClient for
exactly this purpose. Retry/backoff constants are monkeypatched to near-zero
so the 429-then-success test stays fast.
"""

import json

import httpx
import pytest

from investing_agent.services.sources import nse_source as nse_source_module
from investing_agent.services.sources.interfaces import SourceAccessError, SourceTransientError
from investing_agent.services.sources.nse_source import (
    NSEDataSource,
    _classify_announcement,
    _parse_nse_announcement_datetime,
)


def _client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


BEL_ANNOUNCEMENTS = [
    {
        "desc": "Bagging/Receiving of orders/contracts",
        "an_dt": "10-Aug-2026 17:35:59",
        "attchmntFile": "https://nsearchives.nseindia.com/corporate/BEL_order.pdf",
    },
    {
        "desc": "Investor Presentation",
        "an_dt": "30-Aug-2022 13:53:09",
        "attchmntFile": "https://nsearchives.nseindia.com/corporate/BEL_ip.pdf",
    },
    {
        "desc": "Investor Presentation",
        "an_dt": "30-May-2018 12:51:00",
        "attchmntFile": "https://nsearchives.nseindia.com/corporate/BEL_ip.zip",
    },
]


class TestClassifyAnnouncement:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Investor Presentation", "investor_presentation"),
            ("Annual Report for FY26", "annual_report"),
            ("Transcript of Earnings Call", "concall_transcript"),
            ("Con Call Intimation", "concall_transcript"),
            ("Un-Audited Financial Results", "quarterly_result"),
            ("Outcome of Board Meeting - Financial Results", "quarterly_result"),
            ("Disclosure under Regulation 33", "quarterly_result"),
            ("Board Meeting Intimation", "announcement"),
            ("Bagging/Receiving of orders/contracts", "order_contract"),
            ("Change in Director(s)", "announcement"),
            (None, "announcement"),
        ],
    )
    def test_classification(self, text: str | None, expected: str) -> None:
        assert _classify_announcement(text) == expected


class TestParseAnnouncementDatetime:
    def test_parses_dd_mon_yyyy_format(self) -> None:
        dt = _parse_nse_announcement_datetime("13-Aug-2026 17:48:45")
        assert dt is not None
        assert (dt.year, dt.month, dt.day) == (2026, 8, 13)

    def test_parses_iso_like_format(self) -> None:
        dt = _parse_nse_announcement_datetime("2026-08-13 17:48:45")
        assert dt is not None
        assert (dt.year, dt.month, dt.day) == (2026, 8, 13)

    def test_returns_none_for_unparseable(self) -> None:
        assert _parse_nse_announcement_datetime("not a date") is None

    def test_returns_none_for_empty(self) -> None:
        assert _parse_nse_announcement_datetime(None) is None


class TestGetAnnouncements:
    @pytest.mark.asyncio
    async def test_bel_announcement_discovery(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=json.dumps(BEL_ANNOUNCEMENTS).encode())

        source = NSEDataSource(client=_client_for(handler))
        rows = await source.get_announcements("BEL")
        assert len(rows) == 3
        assert rows[0].filing_type == "order_contract"
        assert rows[1].filing_type == "investor_presentation"
        assert rows[0].document_url == "https://nsearchives.nseindia.com/corporate/BEL_order.pdf"
        await source.aclose()

    @pytest.mark.asyncio
    async def test_investor_presentation_filtering(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=json.dumps(BEL_ANNOUNCEMENTS).encode())

        source = NSEDataSource(client=_client_for(handler))
        rows = await source.get_investor_presentations("BEL")
        assert len(rows) == 2
        assert all(r.filing_type == "investor_presentation" for r in rows)
        await source.aclose()

    @pytest.mark.asyncio
    async def test_empty_annual_report_result_no_network_call(self) -> None:
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, content=b"[]")

        source = NSEDataSource(client=_client_for(handler))
        rows = await source.get_annual_reports("BEL")
        assert rows == []
        assert called is False
        await source.aclose()

    @pytest.mark.asyncio
    async def test_malformed_html_response_returns_empty(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>anti-bot challenge page</html>")

        source = NSEDataSource(client=_client_for(handler))
        rows = await source.get_announcements("BEL")
        assert rows == []
        await source.aclose()

    @pytest.mark.asyncio
    async def test_403_raises_source_access_error_without_retry(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(403, content=b"blocked")

        source = NSEDataSource(client=_client_for(handler))
        with pytest.raises(SourceAccessError):
            await source.get_announcements("BEL")
        assert attempts == 1  # never retried
        await source.aclose()

    @pytest.mark.asyncio
    async def test_rate_limit_retries_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(nse_source_module, "_RETRY_WAIT_MIN_SECONDS", 0.001)
        monkeypatch.setattr(nse_source_module, "_RETRY_WAIT_MAX_SECONDS", 0.002)
        monkeypatch.setattr(nse_source_module, "_MIN_REQUEST_GAP_SECONDS", 0.0)
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                return httpx.Response(429, content=b"rate limited")
            return httpx.Response(200, content=json.dumps(BEL_ANNOUNCEMENTS).encode())

        source = NSEDataSource(client=_client_for(handler))
        rows = await source.get_announcements("BEL")
        assert attempts == 3
        assert len(rows) == 3
        await source.aclose()

    @pytest.mark.asyncio
    async def test_retries_exhausted_raises_source_transient_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(nse_source_module, "_RETRY_ATTEMPTS", 2)
        monkeypatch.setattr(nse_source_module, "_RETRY_WAIT_MIN_SECONDS", 0.001)
        monkeypatch.setattr(nse_source_module, "_RETRY_WAIT_MAX_SECONDS", 0.002)
        monkeypatch.setattr(nse_source_module, "_MIN_REQUEST_GAP_SECONDS", 0.0)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=b"server error")

        source = NSEDataSource(client=_client_for(handler))
        with pytest.raises(SourceTransientError):
            await source.get_announcements("BEL")
        await source.aclose()


class TestDownloadAttachment:
    @pytest.mark.asyncio
    async def test_pdf_download_detected_by_magic_bytes(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"%PDF-1.4 fake pdf bytes",
                headers={"content-type": "application/pdf"},
            )

        source = NSEDataSource(client=_client_for(handler))
        attachment = await source.download_attachment("https://nsearchives.nseindia.com/x.pdf")
        assert attachment is not None
        assert attachment.is_pdf is True
        assert attachment.is_zip is False
        await source.aclose()

    @pytest.mark.asyncio
    async def test_zip_attachment_detected_by_magic_bytes(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"PK\x03\x04 fake zip bytes")

        source = NSEDataSource(client=_client_for(handler))
        attachment = await source.download_attachment("https://nsearchives.nseindia.com/x.zip")
        assert attachment is not None
        assert attachment.is_zip is True
        assert attachment.is_pdf is False
        await source.aclose()

    @pytest.mark.asyncio
    async def test_non_pdf_html_challenge_page_not_misclassified(self) -> None:
        """A 200 response whose URL ends in .pdf but whose body is an HTML
        anti-bot challenge page must never be reported as is_pdf=True — magic
        bytes are checked, never the URL extension or content-type header
        alone."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b"<html>please verify you are human</html>",
                headers={"content-type": "application/pdf"},
            )

        source = NSEDataSource(client=_client_for(handler))
        attachment = await source.download_attachment(
            "https://nsearchives.nseindia.com/looks_like.pdf"
        )
        assert attachment is not None
        assert attachment.is_pdf is False
        assert attachment.is_zip is False
        await source.aclose()
