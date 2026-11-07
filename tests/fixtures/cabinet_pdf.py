"""A representative cabinet shop drawing, generated with reportlab.

The fixture is the deterministic stand-in for a real redlined design: a title
block, four part-callout columns, assembly constraints, one in-column tagged
dimension, one shelf-pin hole callout, edge banding, one floating tagged
dimension, and one bare number — every line in a form the parser grammar
documents. Generated at test time (no binary fixture in the repo) so the
layout stays reviewable in code.
"""

from pathlib import Path

from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfgen import canvas

# (x, y-from-bottom, text). Lines are laid out so no two columns share a top
# coordinate — the PDF text layer has no column structure, only geometry.
_LINES: tuple[tuple[float, float, str], ...] = (
    # Constraints, top right.
    (620, 520, "OVERALL WIDTH 800"),
    (620, 500, "OVERALL HEIGHT 770"),
    # Side panel callout column.
    (60, 480, "SIDE PANEL - QTY 2"),
    (60, 464, "18MM BIRCH PLYWOOD"),
    (60, 448, "720 X 380"),
    (60, 432, "DEPTH 380"),
    (60, 416, "SHELF PIN HOLES Ø5 @ 32, 37 FROM FRONT EDGE"),
    # Bare number and a floating tagged dimension — both must surface as
    # unresolved questions, not silently attach. On the right column, clear
    # of every left-column line's height (pdfplumber merges same-line words
    # into one block across the page).
    (700, 380, "500"),
    (700, 360, "REVEAL 3"),
    # Shelf callout column.
    (60, 330, "SHELF - QTY 3"),
    (60, 314, "18MM BIRCH PLYWOOD"),
    (60, 298, "764 X 340"),
    # Top callout column.
    (60, 230, "TOP - QTY 1"),
    (60, 214, "18MM BIRCH PLYWOOD"),
    (60, 198, "800 X 380"),
    (60, 182, "EDGE BANDING: FRONT"),
    # Back panel callout column.
    (60, 130, "BACK PANEL - QTY 1"),
    (60, 114, "12MM BIRCH PLYWOOD"),
    (60, 98, "764 X 720"),
    # Title block.
    (620, 110, "PROJECT: ENTRY-CABINET-001"),
    (620, 94, "UNITS: MM"),
    (620, 78, "SCALE: 1:10"),
    (620, 62, "DATE: 2026-11-07"),
)


def create_cabinet_pdf(path: Path) -> Path:
    """Write the fixture drawing to ``path`` and return it."""
    page_size = landscape(A3)
    pdf = canvas.Canvas(str(path), pagesize=page_size)
    for x, y, text in _LINES:
        pdf.drawString(x, y, text)
    pdf.save()
    return path
