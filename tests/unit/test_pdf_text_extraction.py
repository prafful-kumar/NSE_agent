from __future__ import annotations

"""Unit tests for deterministic PDF text extraction.

hello_world.pdf is a minimal hand-crafted single-page PDF (no proper xref
table — pdfminer's fallback scanner handles that) with the literal text
"Hello World", used so this test never depends on a real filing PDF being
present in the repo.
"""

from pathlib import Path

from investing_agent.services.extraction.pdf_text import (
    PageText,
    deserialize_pages,
    extract_pdf_text,
    serialize_pages,
)

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "pdf" / "hello_world.pdf"


class TestExtractPdfText:
    def test_extracts_text_from_single_page(self) -> None:
        content = _FIXTURE.read_bytes()
        pages = extract_pdf_text(content)
        assert len(pages) == 1
        assert pages[0].page_number == 1
        assert "Hello World" in pages[0].text


class TestSerializeDeserializeRoundTrip:
    def test_round_trips_multiple_pages(self) -> None:
        pages = [PageText(page_number=1, text="first"), PageText(page_number=2, text="second")]
        cached = serialize_pages(pages)
        restored = deserialize_pages(cached)
        assert restored == pages

    def test_round_trips_single_page(self) -> None:
        content = _FIXTURE.read_bytes()
        pages = extract_pdf_text(content)
        restored = deserialize_pages(serialize_pages(pages))
        assert restored == pages
