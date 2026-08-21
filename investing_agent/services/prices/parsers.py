from __future__ import annotations

"""Deterministic CSV parsers for NSE bhavcopy / index close-all files
(Phase 6C.0). No LLM, no network, no adjustment inference -- pure
byte-string-in, structured-rows-out.

Two equity bhavcopy formats are supported, confirmed live against NSE's
archives (see research/provider_evaluation/ for the capability spike that
established these column layouts and the format cutover date):

- NSE_CM_UDIFF ("CM-UDiFF Common Bhavcopy Final"), the current format
  since 2024-07-08. Header:
  TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,
  XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,
  LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,
  ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,
  NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4

- NSE_CM_LEGACY, used through 2024-07-05 (last day both formats were
  available side by side; the legacy URL 404s from 2024-07-08 on). Header:
  SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,
  TIMESTAMP,TOTALTRADES,ISIN

Both are filtered to the cash-market "EQ" series only (excludes
BE/BZ/other series and derivatives) -- the same filter concept expressed
under a different column name in each format (SctySrs vs SERIES).

The index close-all format (NSE_INDICES_CLOSE_ALL) has been stable across
both eras and covers every NSE index for one trading_date in one file.
Header: Index Name,Index Date,Open Index Value,High Index Value,Low Index
Value,Closing Index Value,Points Change,Change(%),Volume,Turnover
(Rs. Cr.),P/E,P/B,Div Yield
"""

import csv
import io
import zipfile
from datetime import date, datetime
from decimal import Decimal

from investing_agent.services.prices.interfaces import ParsedBenchmarkPrice, ParsedDailyPrice

_EQUITY_SERIES = "EQ"

# Internal benchmark_code -> the exact "Index Name" string used in NSE's
# index close-all file. Total-return indices are intentionally not listed:
# NSE's close-all archive publishes the price index but not NIFTY 50 TRI.
# They are ingested through the separately audited Nifty Indices TRI endpoint.
BENCHMARK_INDEX_NAMES: dict[str, str] = {
    "NIFTY_50": "Nifty 50",
}


def _unzip_single_csv(zip_bytes: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        if len(names) != 1:
            raise ValueError(f"expected exactly one file in bhavcopy zip, found {names}")
        return zf.read(names[0])


def parse_udiff_equity_bhavcopy(zip_bytes: bytes) -> list[ParsedDailyPrice]:
    """Parses the current (2024-07-08 onward) CM-UDiFF Common Bhavcopy Final
    zip into per-symbol daily bars, EQ series only."""
    csv_bytes = _unzip_single_csv(zip_bytes)
    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig")))
    out: list[ParsedDailyPrice] = []
    for row in reader:
        if (row.get("SctySrs") or "").strip() != _EQUITY_SERIES:
            continue
        volume_raw = (row.get("TtlTradgVol") or "").strip()
        out.append(
            ParsedDailyPrice(
                symbol=row["TckrSymb"].strip(),
                trading_date=datetime.strptime(row["TradDt"].strip(), "%Y-%m-%d").date(),
                open=Decimal(row["OpnPric"].strip()),
                high=Decimal(row["HghPric"].strip()),
                low=Decimal(row["LwPric"].strip()),
                close=Decimal(row["ClsPric"].strip()),
                volume=int(volume_raw) if volume_raw else None,
            )
        )
    return out


def _parse_legacy_timestamp(raw: str) -> date:
    """Legacy bhavcopy TIMESTAMP is almost always "%d-%b-%Y" (e.g.
    04-JAN-2021), but a small number of 2020-era archived files use a
    2-digit year instead (e.g. 13-Jul-20) -- an NSE archive inconsistency
    confirmed live, not a hypothetical. Both forms are unambiguous for the
    dates this system ever requests (2020s only), so falling back rather
    than guessing a single format keeps the parser deterministic without
    silently dropping affected days."""
    for fmt in ("%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized legacy bhavcopy TIMESTAMP format: {raw!r}")


def parse_legacy_equity_bhavcopy(zip_bytes: bytes) -> list[ParsedDailyPrice]:
    """Parses the pre-2024-07-08 classic bhavcopy zip into per-symbol daily
    bars, EQ series only."""
    csv_bytes = _unzip_single_csv(zip_bytes)
    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig")))
    out: list[ParsedDailyPrice] = []
    for row in reader:
        if (row.get("SERIES") or "").strip() != _EQUITY_SERIES:
            continue
        volume_raw = (row.get("TOTTRDQTY") or "").strip()
        out.append(
            ParsedDailyPrice(
                symbol=row["SYMBOL"].strip(),
                trading_date=_parse_legacy_timestamp(row["TIMESTAMP"].strip()),
                open=Decimal(row["OPEN"].strip()),
                high=Decimal(row["HIGH"].strip()),
                low=Decimal(row["LOW"].strip()),
                close=Decimal(row["CLOSE"].strip()),
                volume=int(volume_raw) if volume_raw else None,
            )
        )
    return out


def parse_index_close_all(csv_bytes: bytes, benchmark_code: str) -> list[ParsedBenchmarkPrice]:
    """Parses one trading_date's index close-all csv, filtered to a single
    benchmark_code's "Index Name" row (usually exactly one row, but the loop
    tolerates zero -- an unrecognized/renamed index name should surface as
    "no data" upstream, never a guess)."""
    index_name = BENCHMARK_INDEX_NAMES.get(benchmark_code)
    if index_name is None:
        raise ValueError(f"unknown benchmark_code {benchmark_code!r}")

    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig")))
    out: list[ParsedBenchmarkPrice] = []
    for row in reader:
        if (row.get("Index Name") or "").strip() != index_name:
            continue
        trading_date: date = datetime.strptime(row["Index Date"].strip(), "%d-%m-%Y").date()
        out.append(
            ParsedBenchmarkPrice(
                benchmark_code=benchmark_code,
                trading_date=trading_date,
                open=Decimal(row["Open Index Value"].strip()),
                high=Decimal(row["High Index Value"].strip()),
                low=Decimal(row["Low Index Value"].strip()),
                close=Decimal(row["Closing Index Value"].strip()),
            )
        )
    return out
