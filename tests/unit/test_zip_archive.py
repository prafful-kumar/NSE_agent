from __future__ import annotations

"""Unit tests for services.extraction.zip_archive — the safety guards around
inspecting a ZIP attachment (zip-bomb caps, zip-slip immunity, junk/unknown
member filtering, corrupt-archive detection). No network, no filesystem
writes — everything operates on in-memory bytes."""

import zipfile
from io import BytesIO

import pytest

from investing_agent.services.extraction import zip_archive as ziparchive
from investing_agent.services.extraction.zip_archive import UnsafeZipError, extract_zip_members


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = BytesIO()
    # ZIP_DEFLATED (not the zipfile default ZIP_STORED) so compress_size
    # actually differs from file_size — required for the compression-ratio
    # guard to have anything real to measure.
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


class TestHappyPath:
    def test_extracts_supported_member(self) -> None:
        content = _make_zip({"presentation.pdf": b"%PDF-1.4 fake"})
        members = extract_zip_members(content)
        assert len(members) == 1
        assert members[0].filename == "presentation.pdf"
        assert members[0].document_type == "pdf"
        assert members[0].content == b"%PDF-1.4 fake"

    def test_extracts_multiple_supported_types(self) -> None:
        content = _make_zip(
            {"a.pdf": b"%PDF-fake", "b.html": b"<html></html>", "c.xml": b"<xml/>"}
        )
        members = extract_zip_members(content)
        types = {m.document_type for m in members}
        assert types == {"pdf", "html", "xml"}


class TestSkippedMembers:
    def test_unsupported_extension_skipped(self) -> None:
        content = _make_zip({"logo.png": b"\x89PNG fake", "doc.pdf": b"%PDF fake"})
        members = extract_zip_members(content)
        assert len(members) == 1
        assert members[0].filename == "doc.pdf"

    def test_macos_resource_fork_skipped_even_with_pdf_extension(self) -> None:
        content = _make_zip(
            {
                "__MACOSX/._presentation.pdf": b"junk applesingle",
                "presentation.pdf": b"%PDF real",
            }
        )
        members = extract_zip_members(content)
        assert len(members) == 1
        assert members[0].filename == "presentation.pdf"

    def test_dotfile_skipped(self) -> None:
        content = _make_zip({".hidden.pdf": b"%PDF fake", "real.pdf": b"%PDF real2"})
        members = extract_zip_members(content)
        assert len(members) == 1
        assert members[0].filename == "real.pdf"

    def test_directory_entries_skipped(self) -> None:
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(zipfile.ZipInfo("subdir/"), "")
            zf.writestr("subdir/file.pdf", b"%PDF nested")
        members = extract_zip_members(buf.getvalue())
        assert len(members) == 1
        assert members[0].filename == "file.pdf"


class TestZipSlipImmunity:
    def test_path_traversal_filename_never_used_as_storage_path(self) -> None:
        """A malicious member name like ../../etc/passwd.pdf must never
        surface as anything other than a basename — extract_zip_members has
        no filesystem side effects at all, so there is nothing to traverse,
        but the returned filename must still be sanitized to a basename."""
        content = _make_zip({"../../../etc/passwd.pdf": b"%PDF fake"})
        members = extract_zip_members(content)
        assert len(members) == 1
        assert members[0].filename == "passwd.pdf"
        assert ".." not in members[0].filename
        assert "/" not in members[0].filename


class TestUnsafeArchives:
    def test_corrupt_zip_raises(self) -> None:
        with pytest.raises(UnsafeZipError):
            extract_zip_members(b"this is not a zip file at all")

    def test_too_many_members_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ziparchive, "_MAX_MEMBERS", 3)
        content = _make_zip({f"file{i}.txt": b"x" for i in range(5)})
        with pytest.raises(UnsafeZipError, match="exceeds cap"):
            extract_zip_members(content)

    def test_oversized_member_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ziparchive, "_MAX_MEMBER_UNCOMPRESSED_BYTES", 10)
        content = _make_zip({"big.txt": b"x" * 1000})
        with pytest.raises(UnsafeZipError, match="per-member cap"):
            extract_zip_members(content)

    def test_oversized_total_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ziparchive, "_MAX_MEMBER_UNCOMPRESSED_BYTES", 10_000)
        monkeypatch.setattr(ziparchive, "_MAX_TOTAL_UNCOMPRESSED_BYTES", 100)
        content = _make_zip({"a.txt": b"x" * 60, "b.txt": b"y" * 60})
        with pytest.raises(UnsafeZipError, match="total uncompressed size"):
            extract_zip_members(content)

    def test_zip_bomb_compression_ratio_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A member that's tiny on disk but claims to inflate to something
        huge (classic zip-bomb shape) must be rejected on ratio, even if it
        would pass the raw size caps."""
        monkeypatch.setattr(ziparchive, "_MAX_COMPRESSION_RATIO", 5)
        # highly compressible content -> real ratio will exceed 5x
        content = _make_zip({"bomb.txt": b"0" * 100_000})
        with pytest.raises(UnsafeZipError, match="compression ratio"):
            extract_zip_members(content)
