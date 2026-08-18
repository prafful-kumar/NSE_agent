from __future__ import annotations

"""Safe ZIP inspection for archived investor-presentation/concall attachments.

NSE occasionally serves a ZIP instead of a bare PDF (e.g. an investor
presentation bundled with a cover letter). This module inspects such a ZIP
and yields only whitelisted document types as separate members — it never
executes, evals, or shells out to anything found inside, and it never trusts
the ZIP's internal member path for writing to disk (callers always re-derive
storage paths from a content hash, never from member.filename).

Guards against zip-bomb / zip-slip style abuse: caps on member count, per-
member and total uncompressed size, and compression ratio, plus a CRC
integrity check via zipfile.testzip() before any member is read.
"""

import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath

_MAX_MEMBERS = 200
_MAX_MEMBER_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 MB
_MAX_TOTAL_UNCOMPRESSED_BYTES = 500 * 1024 * 1024  # 500 MB
_MAX_COMPRESSION_RATIO = 100  # uncompressed / compressed — flags zip bombs

_DOCUMENT_TYPE_BY_EXTENSION = {
    ".pdf": "pdf",
    ".html": "html",
    ".htm": "html",
    ".xml": "xml",
    ".txt": "txt",
}


class UnsafeZipError(Exception):
    """Raised when a ZIP fails a safety check (too many members, oversized,
    bomb-like compression ratio, or a failed CRC integrity check) — the
    archive is rejected outright, never partially extracted."""


@dataclass(frozen=True)
class ExtractedMember:
    filename: str  # basename only, informational — never used as a storage path
    content: bytes
    document_type: str  # pdf|html|xml|txt


def _is_junk_member(basename: str, raw_name: str) -> bool:
    """macOS AppleDouble resource forks (__MACOSX/._foo.pdf) and dotfiles —
    never real document content even when the extension matches."""
    return raw_name.startswith("__MACOSX/") or basename.startswith(".")


def extract_zip_members(content: bytes) -> list[ExtractedMember]:
    """Safely inspects a ZIP archive and returns its whitelisted-type
    members. Non-whitelisted members (images, executables, unknown
    extensions, macOS resource forks) are silently skipped, not extracted.

    Raises UnsafeZipError if the archive itself looks unsafe (bomb-like or
    corrupt) — callers should still archive the parent ZIP's raw bytes
    as-is, but must not attempt extraction in that case.
    """
    try:
        zf = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise UnsafeZipError(f"not a valid zip file: {exc}") from exc

    infos = zf.infolist()
    if len(infos) > _MAX_MEMBERS:
        raise UnsafeZipError(f"zip has {len(infos)} members, exceeds cap of {_MAX_MEMBERS}")

    total_uncompressed = 0
    for info in infos:
        if info.is_dir():
            continue
        total_uncompressed += info.file_size
        if info.file_size > _MAX_MEMBER_UNCOMPRESSED_BYTES:
            raise UnsafeZipError(
                f"member {info.filename!r} uncompressed size {info.file_size} "
                f"exceeds per-member cap of {_MAX_MEMBER_UNCOMPRESSED_BYTES}"
            )
        if info.compress_size > 0:
            ratio = info.file_size / info.compress_size
            if ratio > _MAX_COMPRESSION_RATIO:
                raise UnsafeZipError(
                    f"member {info.filename!r} compression ratio {ratio:.1f}x "
                    f"exceeds cap of {_MAX_COMPRESSION_RATIO}x (possible zip bomb)"
                )
    if total_uncompressed > _MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise UnsafeZipError(
            f"zip total uncompressed size {total_uncompressed} exceeds cap of "
            f"{_MAX_TOTAL_UNCOMPRESSED_BYTES}"
        )

    bad_member = zf.testzip()
    if bad_member is not None:
        raise UnsafeZipError(f"CRC check failed for member {bad_member!r} — archive is corrupt")

    members: list[ExtractedMember] = []
    for info in infos:
        if info.is_dir():
            continue
        # Basename only — never trust the zip's internal path (zip-slip);
        # this filename is informational (e.g. for a title), never a path
        # used to write to disk.
        basename = PurePosixPath(info.filename).name
        if _is_junk_member(basename, info.filename):
            continue
        ext = PurePosixPath(basename).suffix.lower()
        document_type = _DOCUMENT_TYPE_BY_EXTENSION.get(ext)
        if document_type is None:
            continue  # unsupported type (image, executable, unknown) — skip
        member_bytes = zf.read(info)
        members.append(
            ExtractedMember(filename=basename, content=member_bytes, document_type=document_type)
        )

    return members
