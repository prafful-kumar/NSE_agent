from __future__ import annotations

"""Raw document byte storage — the actual Tier-1 archive backing SourceDocument.

Files are written under settings.document_archive_dir, keyed by
{symbol}/{content_hash}.{ext}. storage_path stored on SourceDocument is
relative to document_archive_dir (not an absolute path), so the archive
directory can be relocated without invalidating existing rows.

content_hash is computed by the caller (services/ingestion/common.py) before
calling write_archive — this module never re-derives it, so a single hash
computation stays authoritative for both the DB row and the filename.
"""

from pathlib import Path

from investing_agent.config.settings import get_settings

_EXTENSION_BY_DOCUMENT_TYPE = {
    "pdf": "pdf",
    "html": "html",
    "xml": "xml",
    "xbrl": "xbrl",
    "json": "json",
    "txt": "txt",
    "zip": "zip",
    "csv": "csv",
}


def _archive_root() -> Path:
    return Path(get_settings().document_archive_dir)


def write_archive(content: bytes, symbol: str, content_hash: str, document_type: str) -> str:
    """Writes content bytes under the archive dir, returns the storage_path
    (relative to document_archive_dir). Idempotent: if the file already
    exists (same symbol + content_hash), it is not rewritten."""
    ext = _EXTENSION_BY_DOCUMENT_TYPE.get(document_type, "bin")
    relative_path = f"{symbol.upper()}/{content_hash}.{ext}"
    full_path = _archive_root() / relative_path
    if not full_path.exists():
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
    return relative_path


def read_archive(storage_path: str) -> bytes:
    """Reads back bytes previously written by write_archive."""
    full_path = _archive_root() / storage_path
    return full_path.read_bytes()


def _text_cache_path(storage_path: str) -> Path:
    """The extracted-text cache lives as a sibling .txt file next to the
    archived original — e.g. BEL/<hash>.pdf -> BEL/<hash>.txt. Not tracked as
    a DB column since it's fully re-derivable from the archived bytes."""
    return _archive_root() / Path(storage_path).with_suffix(".txt")


def write_text_cache(storage_path: str, text: str) -> None:
    path = _text_cache_path(storage_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_text_cache(storage_path: str) -> str | None:
    """Returns cached extracted text, or None if no cache exists yet."""
    path = _text_cache_path(storage_path)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")
