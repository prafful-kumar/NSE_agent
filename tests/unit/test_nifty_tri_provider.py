import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from investing_agent.services.prices.nifty_tri import NiftyIndicesTRIBenchmarkPriceProvider
from investing_agent.services.sources.interfaces import SourceAccessError, SourceTransientError


def _client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={})


class TestNiftyIndicesTRIProvider:
    @pytest.mark.asyncio
    async def test_fetches_official_one_day_response_and_parses_terminal_level(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.url.path == "/BackPage/getTotalReturnIndexString"
            assert json.loads(request.content)["cinfo"] == (
                "{'name':'NIFTY 50','startDate':'2021-06-15','endDate':'2021-06-15',"
                "'indexName':'NIFTY 50'}"
            )
            return httpx.Response(
                200,
                json=[
                    {
                        "Index Name": "Nifty 50",
                        "Date": "15 Jun 2021",
                        "TotalReturnsIndex": "22649.05",
                    }
                ],
            )

        provider = NiftyIndicesTRIBenchmarkPriceProvider(client=_client_for(handler))
        raw = await provider.fetch_raw(date(2021, 6, 15))
        assert raw is not None
        rows = provider.parse(raw, date(2021, 6, 15))
        assert len(rows) == 1
        assert rows[0].benchmark_code == "NIFTY_50_TRI"
        assert rows[0].close == Decimal("22649.05")
        assert rows[0].open == rows[0].high == rows[0].low == rows[0].close

    @pytest.mark.asyncio
    async def test_translates_access_and_transient_errors(self) -> None:
        provider = NiftyIndicesTRIBenchmarkPriceProvider(
            client=_client_for(lambda _: httpx.Response(403))
        )
        with pytest.raises(SourceAccessError):
            await provider.fetch_raw(date(2021, 6, 15))

        provider = NiftyIndicesTRIBenchmarkPriceProvider(
            client=_client_for(lambda _: httpx.Response(503))
        )
        with pytest.raises(SourceTransientError):
            await provider.fetch_raw(date(2021, 6, 15))

    def test_parse_range_keeps_the_source_dates(self) -> None:
        provider = NiftyIndicesTRIBenchmarkPriceProvider()
        raw = type("Raw", (), {"content": b'[{"Index Name":"Nifty 50","Date":"16 Jun 2021","TotalReturnsIndex":"1"}]'})()
        assert provider.parse(raw, date(2021, 6, 15)) == []
        assert provider.parse_range(raw)[0].trading_date == date(2021, 6, 16)
