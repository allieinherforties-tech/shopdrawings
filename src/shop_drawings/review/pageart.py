"""Source-page rendering for traceability crops.

Blueprint §4: every value is shown next to the exact place on the source
page it came from. When real page images are configured (rendered PDF
pages), they are served directly; until then — the blueprint's evidence
gap, no sample designs seen yet — this module renders a deterministic
placeholder page from the spec itself: each extracted fact drawn at the
normalized region it was read from, so the traceability loop (value →
crop → text) is exercised end to end.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from shop_drawings.schema import FurnitureSpec, SourceReference

# A4 portrait at 96 dpi.
PAGE_W = 794.0
PAGE_H = 1123.0


def _collect_sources(spec: FurnitureSpec, page: int) -> list[SourceReference]:
    """Every source reference on this page: per-value references plus part context."""
    seen: list[SourceReference] = []
    for part in spec.parts:
        for ref in part.source_references:
            if ref.page == page:
                seen.append(ref)
        for dim in part.dimensions.values():
            if dim.source.page == page:
                seen.append(dim.source)
        for feature in part.features:
            for dim in feature.dimensions.values():
                if dim.source.page == page:
                    seen.append(dim.source)
    for constraint in spec.constraints:
        if constraint.source.page == page:
            seen.append(constraint.source)
    return seen


def _region_box(ref: SourceReference) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = ref.region
    return (x0 * PAGE_W, y0 * PAGE_H, x1 * PAGE_W, y1 * PAGE_H)


def page_svg(spec: FurnitureSpec, page: int) -> str:
    """A placeholder rendering of a source page, with extracted regions marked."""
    parts: list[str] = []
    title = escape(f"{spec.project.name} — {spec.project.source} — page {page}")
    parts.append(
        f'<text x="40" y="48" font-family="ui-monospace, monospace" '
        f'font-size="16" fill="#5a5650">{title}</text>'
    )
    for ref in _collect_sources(spec, page):
        x0, y0, x1, y1 = _region_box(ref)
        w, h = x1 - x0, y1 - y0
        parts.append(
            f'<rect x="{x0:g}" y="{y0:g}" width="{w:g}" height="{h:g}" '
            f'fill="#b3382c14" stroke="#b3382c" stroke-width="1.5"/>'
        )
        label = escape(ref.text)
        font = 13.0 if h < 80 else 20.0
        parts.append(
            f'<text x="{x0 + 8:g}" y="{y0 + h / 2 + font / 3:g}" '
            f'font-family="ui-sans-serif, sans-serif" font-size="{font:g}" '
            f'fill="#1a1d21">{label}</text>'
        )
    body = "\n".join(parts)
    footer = escape("placeholder page — link rendered PDF pages via --source-images for real crops")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {PAGE_W:g} {PAGE_H:g}" '
        f'width="100%" preserveAspectRatio="xMidYMid meet">'
        f'<rect width="100%" height="100%" fill="#ffffff"/>'
        f'<rect x="20" y="20" width="{PAGE_W - 40:g}" height="{PAGE_H - 40:g}" '
        f'fill="none" stroke="#c9c4bc" stroke-width="1"/>'
        f"{body}"
        f'<text x="40" y="{PAGE_H - 40:g}" font-family="ui-monospace, monospace" '
        f'font-size="11" fill="#8a857c">{footer}</text>'
        f"</svg>"
    )


def valid_page(spec: FurnitureSpec, page: int) -> bool:
    """Whether anything in the spec references this page."""
    return bool(_collect_sources(spec, page)) or page == 1
