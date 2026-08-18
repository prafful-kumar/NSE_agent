from __future__ import annotations

"""Deterministic PDF text extraction — no interpretation, no LLM involvement.

extract_pdf_text is purely mechanical: it hands page text back exactly as
pdfplumber pulls it from the PDF's text layer. Scanned/image-only PDFs with
no text layer yield empty strings per page — this module does not OCR, and
callers must never silently invent text for such pages.
"""

from dataclasses import dataclass
from io import BytesIO

import pdfplumber

# Form feed matches the conventional PDF page-break character; chosen so it
# can't plausibly appear inside real extracted body text.
_PAGE_DELIMITER = "\x0c"


@dataclass(frozen=True)
class PageText:
    page_number: int  # 1-indexed
    text: str


def extract_pdf_text(content: bytes) -> list[PageText]:
    """Extracts text page-by-page from raw PDF bytes."""
    pages: list[PageText] = []
    with pdfplumber.open(BytesIO(content)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            pages.append(PageText(page_number=i, text=page.extract_text() or ""))
    return pages


def serialize_pages(pages: list[PageText]) -> str:
    """Joins extracted pages into one string for the on-disk text cache.
    Assumes pages are already ordered/contiguous from page 1 (as
    extract_pdf_text produces) — the delimiter alone is enough to recover
    page boundaries on read."""
    return _PAGE_DELIMITER.join(p.text for p in pages)


def deserialize_pages(cached_text: str) -> list[PageText]:
    return [
        PageText(page_number=i, text=text)
        for i, text in enumerate(cached_text.split(_PAGE_DELIMITER), start=1)
    ]
