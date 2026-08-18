from __future__ import annotations

"""Unit tests for services.storage — the raw document byte archive.

Uses a temp dir (monkeypatched via get_settings, which is lru_cached, so we
patch the module-level accessor instead of mutating the cached Settings
instance) so tests never touch the real ./data/archive.
"""

from pathlib import Path
from unittest.mock import patch

from investing_agent.services.storage import (
    read_archive,
    read_text_cache,
    write_archive,
    write_text_cache,
)


def _patched_settings(tmp_path: Path):
    settings = type("S", (), {"document_archive_dir": str(tmp_path)})()
    return patch("investing_agent.services.storage.get_settings", return_value=settings)


class TestWriteReadArchive:
    def test_write_then_read_round_trips(self, tmp_path: Path) -> None:
        with _patched_settings(tmp_path):
            storage_path = write_archive(b"%PDF-1.4 fake", "BEL", "abc123", "pdf")
            assert storage_path == "BEL/abc123.pdf"
            assert read_archive(storage_path) == b"%PDF-1.4 fake"

    def test_write_is_idempotent(self, tmp_path: Path) -> None:
        with _patched_settings(tmp_path):
            path1 = write_archive(b"first", "BEL", "hash1", "pdf")
            path2 = write_archive(b"first", "BEL", "hash1", "pdf")
            assert path1 == path2
            assert read_archive(path1) == b"first"

    def test_symbol_uppercased_in_path(self, tmp_path: Path) -> None:
        with _patched_settings(tmp_path):
            storage_path = write_archive(b"data", "bel", "hash2", "json")
            assert storage_path == "BEL/hash2.json"

    def test_unknown_document_type_falls_back_to_bin_extension(self, tmp_path: Path) -> None:
        with _patched_settings(tmp_path):
            storage_path = write_archive(b"data", "HAL", "hash3", "weird")
            assert storage_path == "HAL/hash3.bin"


class TestTextCache:
    def test_no_cache_returns_none(self, tmp_path: Path) -> None:
        with _patched_settings(tmp_path):
            assert read_text_cache("BEL/abc123.pdf") is None

    def test_write_then_read_round_trips(self, tmp_path: Path) -> None:
        with _patched_settings(tmp_path):
            write_text_cache("BEL/abc123.pdf", "extracted text\x0cpage two")
            assert read_text_cache("BEL/abc123.pdf") == "extracted text\x0cpage two"

    def test_cache_is_a_sibling_txt_file(self, tmp_path: Path) -> None:
        with _patched_settings(tmp_path):
            write_text_cache("BEL/abc123.pdf", "hello")
            assert (tmp_path / "BEL" / "abc123.txt").read_text() == "hello"
