from __future__ import annotations

"""Unit tests for NSEBhavcopyHistoricalPriceProvider /
NSEIndicesBenchmarkPriceProvider (Phase 6C.0).

Network mocked via httpx.MockTransport, same convention as
test_nse_source_filings.py.
"""

from datetime import date

import httpx
import pytest

from investing_agent.services.prices.nse_bhavcopy import (
    UDIFF_FORMAT_START_DATE,
    NSEBhavcopyHistoricalPriceProvider,
)
from investing_agent.services.prices.nse_indices import NSEIndicesBenchmarkPriceProvider
from investing_agent.services.sources.interfaces import SourceAccessError, SourceTransientError


def _client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestSourceFormatDispatch:
    def test_udiff_on_and_after_cutover(self) -> None:
        provider = NSEBhavcopyHistoricalPriceProvider()
        assert provider.source_format(UDIFF_FORMAT_START_DATE) == "NSE_CM_UDIFF"
        assert provider.source_format(date(2025, 1, 2)) == "NSE_CM_UDIFF"

    def test_legacy_before_cutover(self) -> None:
        provider = NSEBhavcopyHistoricalPriceProvider()
        assert provider.source_format(date(2021, 1, 4)) == "NSE_CM_LEGACY"

    def test_url_uses_udiff_host_on_and_after_cutover(self) -> None:
        provider = NSEBhavcopyHistoricalPriceProvider()
        url = provider._url_for(date(2025, 1, 2))
        assert "nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20250102" in url

    def test_url_uses_legacy_host_before_cutover(self) -> None:
        provider = NSEBhavcopyHistoricalPriceProvider()
        url = provider._url_for(date(2021, 1, 4))
        assert "archives.nseindia.com/content/historical/EQUITIES/2021/JAN/cm04JAN2021bhav" in url


class TestFetchRaw:
    @pytest.mark.asyncio
    async def test_returns_none_on_404_holiday(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        provider = NSEBhavcopyHistoricalPriceProvider(client=_client_for(handler))
        raw = await provider.fetch_raw(date(2025, 1, 26))  # Republic Day
        assert raw is None

    @pytest.mark.asyncio
    async def test_returns_raw_bytes_on_200(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"zip-bytes-here", headers={"content-type": "application/zip"})

        provider = NSEBhavcopyHistoricalPriceProvider(client=_client_for(handler))
        raw = await provider.fetch_raw(date(2025, 1, 2))
        assert raw is not None
        assert raw.content == b"zip-bytes-here"
        assert raw.content_type == "application/zip"

    @pytest.mark.asyncio
    async def test_raises_source_access_error_on_403(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403)

        provider = NSEBhavcopyHistoricalPriceProvider(client=_client_for(handler))
        with pytest.raises(SourceAccessError):
            await provider.fetch_raw(date(2025, 1, 2))

    @pytest.mark.asyncio
    async def test_raises_source_transient_error_on_503(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        provider = NSEBhavcopyHistoricalPriceProvider(client=_client_for(handler))
        with pytest.raises(SourceTransientError):
            await provider.fetch_raw(date(2025, 1, 2))


class TestBenchmarkFetchRaw:
    @pytest.mark.asyncio
    async def test_returns_none_on_404(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        provider = NSEIndicesBenchmarkPriceProvider("NIFTY_50", client=_client_for(handler))
        raw = await provider.fetch_raw(date(2025, 1, 26))
        assert raw is None

    @pytest.mark.asyncio
    async def test_returns_raw_bytes_on_200(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"csv-bytes-here", headers={"content-type": "text/csv"})

        provider = NSEIndicesBenchmarkPriceProvider("NIFTY_50", client=_client_for(handler))
        raw = await provider.fetch_raw(date(2025, 1, 2))
        assert raw is not None
        assert raw.content == b"csv-bytes-here"

    def test_url_uses_ddmmyyyy_format(self) -> None:
        provider = NSEIndicesBenchmarkPriceProvider("NIFTY_50")
        url = provider._url_for(date(2025, 1, 2))
        assert url.endswith("ind_close_all_02012025.csv")
