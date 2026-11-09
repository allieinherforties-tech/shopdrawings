"""Conflict detection — the 'Conflicts' section of the review queue.

Blueprint §4: when two extracted values disagree, both are shown with their
source crops and the constraint that failed. v1 detects the disagreements
that are computable from the spec alone:

- **Text/value mismatch** — the number in the source text does not match
  the extracted value (a misread, or a value typed over silently).
- **Material/thickness mismatch** — a material string like ``18mm birch
  plywood`` disagrees with the part's ``thickness`` dimension.

Both are real, generic, and computable without knowing the construction.
"""

from __future__ import annotations

import re

from shop_drawings.review.models import Conflict, ValueAddress
from shop_drawings.schema import Dimension, DimensionKind, FurnitureSpec, Part

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_MM_IN_MATERIAL = re.compile(r"(\d+(?:\.\d+)?)\s*mm")


def _numbers_in(text: str) -> list[float]:
    return [float(n) for n in _NUMBER.findall(text)]


def text_value_mismatch(address: ValueAddress, dim: Dimension) -> Conflict | None:
    """The extracted value disagrees with every number in its own source text."""
    numbers = _numbers_in(dim.source.text)
    if not numbers:
        return None
    if any(abs(n - dim.value) <= dim.tolerance + 1e-9 for n in numbers):
        return None
    shown = ", ".join(f"{n:g}" for n in numbers)
    return Conflict(
        kind="text_value_mismatch",
        description=(
            f"Extracted value {dim.value:g} but its source text reads {shown} "
            f"(\u201c{dim.source.text}\u201d) \u2014 likely a misread."
        ),
        addresses=[address],
        sources=[dim.source],
    )


def material_thickness_mismatch(part: Part) -> Conflict | None:
    """A material like ``18mm birch plywood`` disagrees with the thickness dimension."""
    match = _MM_IN_MATERIAL.search(part.material)
    thickness = part.dimensions.get(DimensionKind.THICKNESS)
    if match is None or thickness is None:
        return None
    declared = float(match.group(1))
    if abs(declared - thickness.value) <= thickness.tolerance + 1e-9:
        return None
    return Conflict(
        kind="material_thickness_mismatch",
        description=(
            f"Part '{part.id}': material '{part.material}' implies {declared:g}mm "
            f"but the extracted thickness is {thickness.value:g}."
        ),
        addresses=[ValueAddress(part_id=part.id, kind=DimensionKind.THICKNESS)],
        sources=[thickness.source],
    )


def detect_conflicts(spec: FurnitureSpec) -> list[Conflict]:
    """Every computable disagreement in the spec, in spec order."""
    conflicts: list[Conflict] = []
    for part in spec.parts:
        material_conflict = material_thickness_mismatch(part)
        if material_conflict is not None:
            conflicts.append(material_conflict)
        for kind, dim in part.dimensions.items():
            mismatch = text_value_mismatch(ValueAddress(part_id=part.id, kind=kind), dim)
            if mismatch is not None:
                conflicts.append(mismatch)
        for index, feature in enumerate(part.features):
            for kind, dim in feature.dimensions.items():
                mismatch = text_value_mismatch(
                    ValueAddress(part_id=part.id, feature_index=index, kind=kind),
                    dim,
                )
                if mismatch is not None:
                    conflicts.append(mismatch)
    return conflicts
