"""Design ingestion: a PDF or image file becomes a ``DesignDocument``.

This is the pipeline's I/O edge for inputs. PDFs contribute a text layer
(pdfplumber) — the deterministic extraction path — and every page is rendered
to a PNG (pypdfium2) for the vision path. Sketches and photos arrive as a
single image page with no text layer, so they take exactly the vision path a
PDF page would; the blueprint calls this assisted extraction.

Regions are normalized with the origin at the top-left, matching
``SourceReference`` and the raster coordinates a vision model reports.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Final

import pdfplumber
import pypdfium2 as pdfium

from .geometry import Region
from .models import DesignDocument, PageImage, TextBlock

#: Render scale: 2.0 ≈ 144 dpi on a 72 dpi page — enough for a vision model
#: to read dimension text without four-figure PNGs per sheet.
RENDER_SCALE: Final = 2.0

_PDF_SUFFIXES: Final = {".pdf"}


def _region_from_bbox(
    x0: float, top: float, x1: float, bottom: float, width: float, height: float
) -> Region:
    """pdfplumber bbox (points, top-left origin) → normalized region, padded."""
    region = Region(x0 / width, top / height, x1 / width, bottom / height)
    return region.padded()


def _harvest_page_blocks(page: pdfplumber.page.Page, page_number: int) -> list[TextBlock]:
    """One PDF page's words, grouped into visual lines with regions."""
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (round(w["top"], 1), w["x0"]))
    lines: list[list[dict[str, float]]] = []
    for word in ordered:
        line = lines[-1] if lines else None
        if line is not None and abs(word["top"] - line[-1]["top"]) <= 2.0:
            line.append(word)
        else:
            lines.append([word])
    blocks: list[TextBlock] = []
    page_width, page_height = page.width, page.height
    for line in lines:
        text = " ".join(str(w["text"]) for w in line).strip()
        if not text:
            continue
        region = _region_from_bbox(
            min(w["x0"] for w in line),
            min(w["top"] for w in line),
            max(w["x1"] for w in line),
            max(w["bottom"] for w in line),
            page_width,
            page_height,
        )
        blocks.append(TextBlock(page=page_number, text=text, region=region))
    return blocks


def _render_pages(path: Path) -> tuple[PageImage, ...]:
    """Render every PDF page to PNG bytes at ``RENDER_SCALE``."""
    pages: list[PageImage] = []
    pdf = pdfium.PdfDocument(str(path))
    try:
        for index, page in enumerate(pdf):
            bitmap = page.render(scale=RENDER_SCALE)
            image = bitmap.to_pil()
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            pages.append(PageImage(page=index + 1, png=buffer.getvalue()))
    finally:
        pdf.close()
    return tuple(pages)


_IMAGE_MEDIA_TYPES: Final = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def ingest_design(path: Path) -> DesignDocument:
    """Read a PDF or image design into pages and text blocks.

    Raises ``ValueError`` on unsupported suffixes; ``FileNotFoundError``
    propagates for a missing file — the caller owns messaging.
    """
    suffix = path.suffix.lower()
    if suffix in _IMAGE_MEDIA_TYPES:
        return DesignDocument(
            source_name=path.name,
            pages=(
                PageImage(page=1, png=path.read_bytes(), media_type=_IMAGE_MEDIA_TYPES[suffix]),
            ),
            blocks=(),
        )
    if suffix not in _PDF_SUFFIXES and suffix not in _IMAGE_MEDIA_TYPES:
        raise ValueError(
            f"unsupported design input {path.name!r}: expected a PDF or an image "
            f"({', '.join(sorted(_IMAGE_MEDIA_TYPES))})"
        )

    with pdfplumber.open(path) as pdf:
        blocks: list[TextBlock] = []
        for number, page in enumerate(pdf.pages, start=1):
            blocks.extend(_harvest_page_blocks(page, number))
    return DesignDocument(
        source_name=path.name,
        pages=_render_pages(path),
        blocks=tuple(blocks),
    )
