from __future__ import annotations

"""Unit tests for ZerodhaStatementImporter (no database).

Synthetic workbooks mirror the real Zerodha Console export structure
confirmed by inspecting actual tradebook/ledger files under statements/
(header row located dynamically by column-name signature, not a fixed
row index -- these tests exercise that same matching logic with a
shorter, synthetic metadata block)."""

from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from investing_agent.services.ingestion.broker_history.zerodha import (
    LEDGER_HEADER,
    TRADEBOOK_HEADER,
    ZerodhaStatementImporter,
)


def _write_workbook(path: Path, sheet_name: str, header: tuple, rows: list[tuple]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append((None, "Client ID", "TESTID"))
    ws.append((None, "Title row"))
    ws.append((None, *header))
    for row in rows:
        ws.append((None, *row))
    wb.save(path)


class TestTradebookParsing:
    def test_parses_buy_and_sell_trades(self, tmp_path) -> None:
        path = tmp_path / "tradebook-TEST-EQ.xlsx"
        _write_workbook(
            path,
            "Equity",
            TRADEBOOK_HEADER,
            [
                (
                    "INFY", "INE009A01021", "2024-01-05", "NSE", "EQ", "EQ",
                    "buy", False, 10.0, 1500.5, "T1", "O1", "2024-01-05T10:00:00",
                ),
                (
                    "TCS", "INE467B01029", "2024-02-10", "NSE", "EQ", "EQ",
                    "sell", False, 5.0, 3800.25, "T2", "O2", "2024-02-10T11:30:00",
                ),
            ],
        )

        result = ZerodhaStatementImporter().parse(path)

        assert result.source_type == "zerodha_tradebook_xlsx"
        assert len(result.trades) == 2
        assert result.date_range_start.isoformat() == "2024-01-05"
        assert result.date_range_end.isoformat() == "2024-02-10"

        buy, sell = result.trades
        assert buy.dedupe_key == "T1"
        assert buy.symbol == "INFY"
        assert buy.side == "BUY"
        assert buy.quantity == Decimal("10")
        assert buy.price == Decimal("1500.5")
        assert sell.side == "SELL"
        assert not result.warnings

    def test_unrecognized_trade_type_is_skipped_with_warning(self, tmp_path) -> None:
        path = tmp_path / "tradebook-TEST-EQ.xlsx"
        _write_workbook(
            path,
            "Equity",
            TRADEBOOK_HEADER,
            [
                (
                    "INFY", "INE009A01021", "2024-01-05", "NSE", "EQ", "EQ",
                    "bonus", False, 10.0, 0.0, "T1", "O1", "2024-01-05T10:00:00",
                ),
            ],
        )

        result = ZerodhaStatementImporter().parse(path)

        assert result.trades == []
        assert len(result.warnings) == 1
        assert "T1" in result.warnings[0]


class TestLedgerParsing:
    def test_maps_voucher_types_and_skips_balance_markers(self, tmp_path) -> None:
        path = tmp_path / "ledger-TEST.xlsx"
        _write_workbook(
            path,
            "Equity",
            LEDGER_HEADER,
            [
                ("Opening Balance", "", "", "", "", "", 0.0),
                ("Funds added via UPI", "2024-01-01", "NSE-EQ - Z", "Bank Receipts", 0.0, 1000.0, 1000.0),
                ("AMC charge", "2024-01-02", "NSE-EQ - Z", "Journal Entry", 50.0, 0.0, 950.0),
                ("Net settlement", "2024-01-03", "NSE-EQ - Z", "Book Voucher", 0.0, 200.0, 1150.0),
                ("Funds withdrawn", "2024-01-04", "NSE-EQ - Z", "Bank Payments", 1150.0, 0.0, 0.0),
                ("Closing Balance", "", "", "", "", "", 0.0),
            ],
        )

        result = ZerodhaStatementImporter().parse(path)

        assert result.source_type == "zerodha_ledger_xlsx"
        assert len(result.cash_flows) == 4
        assert not result.warnings

        deposit, charge, settlement, withdrawal = result.cash_flows
        assert deposit.flow_type == "DEPOSIT"
        assert deposit.amount == Decimal("1000")
        assert charge.flow_type == "CHARGE"
        assert charge.amount == Decimal("-50")
        assert settlement.flow_type == "OTHER"
        assert withdrawal.flow_type == "WITHDRAWAL"
        assert withdrawal.amount == Decimal("-1150")

    def test_running_balance_mismatch_produces_warning(self, tmp_path) -> None:
        path = tmp_path / "ledger-TEST.xlsx"
        _write_workbook(
            path,
            "Equity",
            LEDGER_HEADER,
            [
                ("Opening Balance", "", "", "", "", "", 0.0),
                ("Funds added via UPI", "2024-01-01", "NSE-EQ - Z", "Bank Receipts", 0.0, 1000.0, 5000.0),
            ],
        )

        result = ZerodhaStatementImporter().parse(path)

        assert len(result.warnings) == 1
        assert "mismatch" in result.warnings[0]

    def test_dividend_keyword_warns_but_is_not_fabricated_as_dividend(self, tmp_path) -> None:
        path = tmp_path / "ledger-TEST.xlsx"
        _write_workbook(
            path,
            "Equity",
            LEDGER_HEADER,
            [
                ("Opening Balance", "", "", "", "", "", 0.0),
                ("Dividend received for INFY", "2024-01-01", "NSE-EQ - Z", "Book Voucher", 0.0, 100.0, 100.0),
            ],
        )

        result = ZerodhaStatementImporter().parse(path)

        assert result.dividends == []
        assert len(result.cash_flows) == 1
        assert any("dividend" in w.lower() for w in result.warnings)

    def test_unrecognized_voucher_type_defaults_to_other_with_warning(self, tmp_path) -> None:
        path = tmp_path / "ledger-TEST.xlsx"
        _write_workbook(
            path,
            "Equity",
            LEDGER_HEADER,
            [
                ("Opening Balance", "", "", "", "", "", 0.0),
                ("Mystery entry", "2024-01-01", "NSE-EQ - Z", "Something New", 0.0, 10.0, 10.0),
            ],
        )

        result = ZerodhaStatementImporter().parse(path)

        assert result.cash_flows[0].flow_type == "OTHER"
        assert any("Something New" in w for w in result.warnings)


class TestUnrecognizedFile:
    def test_raises_on_unmatched_header(self, tmp_path) -> None:
        path = tmp_path / "pnl-TEST.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Equity"
        ws.append((None, "Symbol", "Buy Value", "Sell Value", "Realized P&L"))
        ws.append((None, "INFY", 1000.0, 1200.0, 200.0))
        wb.save(path)

        with pytest.raises(ValueError, match="Unrecognized Zerodha statement format"):
            ZerodhaStatementImporter().parse(path)
