from __future__ import annotations

"""Unit tests for deterministic NSE bhavcopy / index parsers (Phase 6C.0).

Fixture bytes are synthetic but match the exact real column headers
confirmed live during the capability spike (research/provider_evaluation/)
against nsearchives.nseindia.com and archives.nseindia.com -- built inline
rather than checked in as binary files, same convention as the Zerodha
importer's synthetic-but-real-format test workbooks.
"""

import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from investing_agent.services.prices.parsers import (
    parse_index_close_all,
    parse_legacy_equity_bhavcopy,
    parse_udiff_equity_bhavcopy,
)


def _zip_csv(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, text)
    return buf.getvalue()


_UDIFF_CSV = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
    "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
    "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
    "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
    "2025-01-02,2025-01-02,CM,NSE,STK,383,INE263A01024,BEL,EQ,,,,,BHARAT ELECTRONICS LTD,"
    "294.45,297.80,291.35,296.80,296.30,293.90,,296.80,,,13361957,3941928990.40,108260,F1,1,,,,,\n"
    "2025-01-02,2025-01-02,CM,NSE,STK,999,INE111A01011,SOMEFUT,FUT,,,,,SOME FUTURE,"
    "100.00,101.00,99.00,100.50,100.50,99.90,,100.50,,,5000,500000.00,10,F1,1,,,,,\n"
)

_LEGACY_CSV = (
    "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,"
    "TOTALTRADES,ISIN,\n"
    "BEL,EQ,129,131.8,126.9,131.3,131.4,126.4,35204855,4564357489.7,04-JAN-2021,128351,"
    "INE263A01024,\n"
    "BEL,BE,50,51,49,50.5,50.5,49.9,1000,50500,04-JAN-2021,10,INE263A01024,\n"
)

_INDEX_CSV = (
    "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,"
    "Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield\n"
    "Nifty 50,02-01-2025,23783,24226.7,23751.55,24188.65,445.75,1.88,283200811,32237.25,"
    "22.29,3.61,1.25\n"
    "Nifty Bank,02-01-2025,50000,50500,49800,50300,300,0.6,1000000,5000,20,3,1\n"
)


class TestParseUdiffEquityBhavcopy:
    def test_parses_and_filters_to_eq_series(self):
        zip_bytes = _zip_csv("BhavCopy_NSE_CM.csv", _UDIFF_CSV)
        rows = parse_udiff_equity_bhavcopy(zip_bytes)
        assert len(rows) == 1  # the FUT row is excluded
        row = rows[0]
        assert row.symbol == "BEL"
        assert row.trading_date == date(2025, 1, 2)
        assert row.open == Decimal("294.45")
        assert row.high == Decimal("297.80")
        assert row.low == Decimal("291.35")
        assert row.close == Decimal("296.80")
        assert row.volume == 13361957

    def test_rejects_zip_with_multiple_files(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.csv", _UDIFF_CSV)
            zf.writestr("b.csv", _UDIFF_CSV)
        with pytest.raises(ValueError, match="expected exactly one file"):
            parse_udiff_equity_bhavcopy(buf.getvalue())


class TestParseLegacyEquityBhavcopy:
    def test_parses_and_filters_to_eq_series(self):
        zip_bytes = _zip_csv("cm04JAN2021bhav.csv", _LEGACY_CSV)
        rows = parse_legacy_equity_bhavcopy(zip_bytes)
        assert len(rows) == 1  # the BE-series row is excluded
        row = rows[0]
        assert row.symbol == "BEL"
        assert row.trading_date == date(2021, 1, 4)
        assert row.open == Decimal("129")
        assert row.close == Decimal("131.3")
        assert row.volume == 35204855

    def test_parses_2020_era_two_digit_year_timestamp(self):
        # Confirmed live: some 2020 archived NSE bhavcopy files use TIMESTAMP
        # "13-Jul-20" (2-digit year) rather than the usual "13-JUL-2020".
        csv_2020 = (
            "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,"
            "TOTALTRADES,ISIN\n"
            "BEL,EQ,90,92,89,91.5,91.4,89.9,1000000,90000000,13-Jul-20,5000,INE263A01024\n"
        )
        zip_bytes = _zip_csv("cm13JUL2020bhav.csv", csv_2020)
        rows = parse_legacy_equity_bhavcopy(zip_bytes)
        assert len(rows) == 1
        assert rows[0].trading_date == date(2020, 7, 13)


class TestParseIndexCloseAll:
    def test_filters_to_requested_benchmark_code(self):
        rows = parse_index_close_all(_INDEX_CSV.encode(), "NIFTY_50")
        assert len(rows) == 1  # the Nifty Bank row is excluded
        row = rows[0]
        assert row.benchmark_code == "NIFTY_50"
        assert row.trading_date == date(2025, 1, 2)
        assert row.open == Decimal("23783")
        assert row.close == Decimal("24188.65")

    def test_unknown_benchmark_code_raises(self):
        with pytest.raises(ValueError, match="unknown benchmark_code"):
            parse_index_close_all(_INDEX_CSV.encode(), "SENSEX")

    def test_no_matching_rows_returns_empty_not_a_guess(self):
        csv_without_nifty = (
            "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,"
            "Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,"
            "P/B,Div Yield\n"
            "Nifty Bank,02-01-2025,50000,50500,49800,50300,300,0.6,1000000,5000,20,3,1\n"
        )
        rows = parse_index_close_all(csv_without_nifty.encode(), "NIFTY_50")
        assert rows == []
