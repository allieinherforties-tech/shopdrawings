"""Shared helpers for reading a part's extents.

Both constraint closure and geometric sanity need to ask "how big is this
part along its named extents?" — this module is the one place that answers,
so the two checks can never disagree about what a part's extents are.
"""

from __future__ import annotations

from shop_drawings.schema import DimensionKind, Part

#: Extent kinds describe how much space a part occupies; the other kinds
#: (hole diameters, setbacks, reveals, ...) describe features, not bulk.
EXTENT_KINDS: tuple[DimensionKind, ...] = (
    DimensionKind.LENGTH,
    DimensionKind.WIDTH,
    DimensionKind.THICKNESS,
    DimensionKind.DEPTH,
    DimensionKind.HEIGHT,
)


def extent_kinds_of(part: Part) -> list[DimensionKind]:
    """The part's declared extent dimensions, in the schema's kind order."""
    return [kind for kind in EXTENT_KINDS if kind in part.dimensions]


def extent_value(part: Part, kind: DimensionKind) -> float:
    """The part's value for one extent kind. Caller guarantees presence."""
    return part.dimensions[kind].value


def face_extents_of(part: Part) -> list[float]:
    """The part's face dimensions — its extents excluding thickness."""
    return [
        extent_value(part, kind)
        for kind in extent_kinds_of(part)
        if kind is not DimensionKind.THICKNESS
    ]
