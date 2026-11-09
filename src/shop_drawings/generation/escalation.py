"""Escalation packages — what the pipeline emits instead of guessed geometry.

A part the v1 primitive library cannot resolve does not fail the job and
never receives fabricated geometry (blueprint §3). It gets a complete
package a modeler can finish in Rhino without starting from a blank page:

- ``package.json`` — the full approved spec, the part's own record, the
  blocking reasons (why the library could not resolve it), materials,
  joinery notes, and any open questions carried by the spec;
- ``views.dxf`` — orthographic projections of the part's *declared*
  extents (plan, front elevation, side elevation) with dimension labels.

The views draw exactly what the spec states — the declared extents — and
nothing the extractor did not; that is what keeps an escalation honest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ezdxf.colors import RGB
from ezdxf.filemanagement import new

from shop_drawings.generation.config import DXF_INSUNITS
from shop_drawings.generation.cutlist import fmt_dimension
from shop_drawings.generation.geometry import extents_in_order
from shop_drawings.schema import FurnitureSpec, Part, Units

#: Layer names inside an escalation views.dxf (a documentation drawing,
#: not a fabrication file — deliberately outside the CUT/POCKET/DRILL
#: convention, which is reserved for files CAM consumes).
VIEW_LAYER = "VIEW"
TEXT_LAYER = "TEXT"

ESCALATION_DIR = "escalation"


@dataclass(frozen=True)
class Escalation:
    """One escalated part and the package written for it."""

    part_id: str
    reasons: tuple[str, ...]
    directory: Path
    files: tuple[Path, ...] = ()


def _projected_rectangles(part: Part) -> list[tuple[float, float]]:
    """The three orthographic extents the part declares, as (w, h) pairs."""
    values = [dim.value for _, dim in extents_in_order(part)]
    length = values[0] if len(values) > 0 else 0.0
    width = values[1] if len(values) > 1 else 0.0
    thickness = values[2] if len(values) > 2 else 0.0
    return [(length, width), (length, thickness), (width, thickness)]


def _write_views(part: Part, path: Path, units: Units) -> None:
    """Dimension-labeled orthographic projections of the declared extents."""
    doc = new("R2018", setup=True)
    doc.header["$INSUNITS"] = DXF_INSUNITS[units]
    doc.layers.add(VIEW_LAYER)
    doc.layers.get(VIEW_LAYER).rgb = RGB(0, 0, 255)
    doc.layers.add(TEXT_LAYER)
    doc.layers.get(TEXT_LAYER).rgb = RGB(0, 0, 0)
    msp = doc.modelspace()

    projections = _projected_rectangles(part)
    gap = 100.0
    cursor_x = 0.0
    labels = ("plan (length x width)", "front (length x thickness)", "side (width x thickness)")
    for (extent_x, extent_y), label in zip(projections, labels, strict=True):
        if extent_x > 0 and extent_y > 0:
            msp.add_lwpolyline(
                [
                    (cursor_x, 0),
                    (cursor_x + extent_x, 0),
                    (cursor_x + extent_x, extent_y),
                    (cursor_x, extent_y),
                ],
                format="xy",
                close=True,
                dxfattribs={"layer": VIEW_LAYER},
            )
        text = msp.add_text(
            f"{label}: {fmt_dimension(extent_x)} x {fmt_dimension(extent_y)}",
            dxfattribs={"layer": TEXT_LAYER, "height": 15},
        )
        text.set_placement((cursor_x, -40))
        cursor_x += extent_x + gap
    doc.saveas(path)


def write_escalation_package(
    spec: FurnitureSpec, part: Part, reasons: list[str], out_dir: Path
) -> Escalation:
    """Write the escalation package for one part; returns its record."""
    directory = out_dir / ESCALATION_DIR / part.id
    directory.mkdir(parents=True, exist_ok=True)

    package = {
        "part_id": part.id,
        "reasons": reasons,
        "materials": part.material,
        "joinery_notes": [feature.note for feature in part.features if feature.note],
        "open_questions": spec.unresolved_questions,
        "part": part.model_dump(mode="json", by_alias=True),
        "spec": spec.model_dump(mode="json", by_alias=True),
    }
    package_path = directory / "package.json"
    package_path.write_text(json.dumps(package, indent=2), encoding="utf-8")

    views_path = directory / "views.dxf"
    _write_views(part, views_path, spec.project.units)

    return Escalation(
        part_id=part.id,
        reasons=tuple(reasons),
        directory=directory,
        files=(package_path, views_path),
    )
