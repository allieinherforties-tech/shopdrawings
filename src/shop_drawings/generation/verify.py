"""Automatic verification — the checks that run on every job (blueprint §5).

Five families, all enforced here in code:

- **Traceability** — every dimension and constraint carries a source
  reference. The schema makes untraceable values unconstructible; this
  re-asserts it on the way into generation.
- **Geometric sanity** — features fit inside their panel, run holes do
  not overlap, parts match their declared stock thickness.
- **Constraint closure** — *skipped in v1 with a stated reason*: the
  schema's constraints carry no composition relations (which parts sum to
  an overall width), so closure cannot be evaluated honestly here. The
  validation stage owns it; the note travels with every report so the
  gap stays visible rather than forgotten.
- **Fabrication-file checks** — the written DXFs re-open with correct
  units, declared layers, closed contours, no duplicate outlines, and
  every part inside the sheet's usable area.
- **.3dm round-trip** — the shipped file is regenerated from the same
  approved spec and compared by canonical content digest (names, layers,
  user strings, geometry). rhino3dm embeds random object UUIDs and
  varying archive metadata, so byte equality is unattainable; the digest
  catches hand-edit drift exactly as the blueprint intends, and the
  deviation is asserted by test.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import rhino3dm
from ezdxf.entities.circle import Circle
from ezdxf.entities.lwpolyline import LWPolyline
from ezdxf.filemanagement import readfile
from ezdxf.layouts.base import BaseLayout

from shop_drawings.generation.config import DXF_INSUNITS, GenerationConfig
from shop_drawings.generation.geometry import PartPlan
from shop_drawings.generation.model3dm import build_model
from shop_drawings.schema import FurnitureSpec, PartType, Units

#: Thickness (in spec units) a part may deviate from its material's
#: declared nominal thickness before the stock check fails.
_STOCK_TOLERANCE = 1e-6

_MATERIAL_MM_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*mm")


class CheckStatus(StrEnum):
    """Outcome of one automatic check."""

    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class CheckResult:
    """One check's outcome: status plus human-readable detail."""

    name: str
    status: CheckStatus
    failures: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """A job ships only when every check that ran has passed."""
        return self.status is CheckStatus.PASS


def _material_nominal_thickness(material: str) -> float | None:
    """Nominal thickness declared in the material string, if it states one in mm."""
    match = _MATERIAL_MM_PATTERN.search(material)
    return float(match.group(1)) if match else None


def check_traceability(spec: FurnitureSpec) -> CheckResult:
    """Every dimension and constraint traces to a source page and region."""
    failures: list[str] = []
    for part in spec.parts:
        for kind, dim in part.dimensions.items():
            if dim.source is None:  # pragma: no cover - schema prevents this
                failures.append(f"part '{part.id}' dimension '{kind.value}' has no source")
        for feature in part.features:
            for kind, dim in feature.dimensions.items():
                if dim.source is None:  # pragma: no cover - schema prevents this
                    failures.append(
                        f"part '{part.id}' feature '{feature.type.value}' "
                        f"dimension '{kind.value}' has no source"
                    )
    for constraint in spec.constraints:
        if constraint.source is None:  # pragma: no cover - schema prevents this
            failures.append(f"constraint '{constraint.type.value}' has no source")
    status = CheckStatus.FAIL if failures else CheckStatus.PASS
    return CheckResult(name="traceability", status=status, failures=tuple(failures))


def check_geometric_sanity(spec: FurnitureSpec, plans: list[PartPlan]) -> CheckResult:
    """Features fit their part; run holes do not overlap; stock thickness agrees."""
    failures: list[str] = []
    notes: list[str] = []
    parts_by_id = {part.id: part for part in spec.parts}
    for plan in plans:
        if plan.part_type is PartType.HARDWARE:
            continue
        if plan.thickness <= 0:
            failures.append(f"part '{plan.part_id}' thickness must be positive")
        for drill in plan.drills:
            radius = drill.diameter / 2
            if not (radius < drill.center_x and drill.center_x + radius < plan.length):
                failures.append(
                    f"part '{plan.part_id}' drill at x={drill.center_x:g} "
                    "reaches past the panel's run-axis edge"
                )
            if not (radius < drill.center_y and drill.center_y + radius < plan.width):
                failures.append(
                    f"part '{plan.part_id}' drill at y={drill.center_y:g} "
                    "reaches past the panel's offset-axis edge"
                )
        # A run of same-diameter holes on one column must not overlap.
        runs: dict[tuple[float, float], list[float]] = {}
        for drill in plan.drills:
            runs.setdefault((drill.center_y, drill.diameter), []).append(drill.center_x)
        for (center_y, diameter), centers in runs.items():
            centers.sort()
            for before, after in itertools.pairwise(centers):
                if after - before <= diameter:
                    failures.append(
                        f"part '{plan.part_id}' drills overlap on the y={center_y:g} column "
                        f"(diameter {diameter:g}, gap {after - before:g})"
                    )
        for pocket in plan.pockets:
            if pocket.depth > plan.thickness:
                failures.append(
                    f"part '{plan.part_id}' pocket cut depth {pocket.depth:g} "
                    f"exceeds thickness {plan.thickness:g}"
                )
            if (
                pocket.x < 0
                or pocket.y < 0
                or pocket.x + pocket.length > plan.length + 1e-9
                or pocket.y + pocket.width > plan.width + 1e-9
            ):
                failures.append(
                    f"part '{plan.part_id}' pocket at ({pocket.x:g}, {pocket.y:g}) "
                    "extends past the panel edge"
                )
        part = parts_by_id[plan.part_id]
        nominal = _material_nominal_thickness(part.material)
        if nominal is None:
            notes.append(
                f"part '{plan.part_id}': material '{part.material}' states no mm "
                "thickness — stock match not evaluated"
            )
        elif abs(plan.thickness - nominal) > _STOCK_TOLERANCE:
            failures.append(
                f"part '{plan.part_id}' thickness {plan.thickness:g} does not match "
                f"declared stock {nominal:g} in '{part.material}'"
            )
    status = CheckStatus.FAIL if failures else CheckStatus.PASS
    return CheckResult(
        name="geometric_sanity", status=status, failures=tuple(failures), notes=tuple(notes)
    )


def check_constraint_closure(spec: FurnitureSpec) -> CheckResult:
    """Assembly-level closure — skipped: the schema carries no composition data.

    The blueprint's closure example (parts summing to an 800mm overall
    width) needs to know *which* parts compose a constraint. The v1
    schema's ``Constraint`` has no member list, so no honest sum exists;
    closure runs in the validation stage where those relations live. The
    skip is loud so the gap cannot be forgotten.
    """
    return CheckResult(
        name="constraint_closure",
        status=CheckStatus.SKIPPED,
        notes=(
            "schema constraints carry no composition relations (which parts sum to "
            "the whole); closure is evaluated by the validation stage, not generation",
        ),
    )


def _rounded(value: float) -> float:
    return round(value, 6)


def rhino3dm_read(path: Path) -> rhino3dm.File3dm:
    """Read a .3dm file, failing loudly on a missing or corrupt archive."""
    model = rhino3dm.File3dm.Read(str(path))
    if model is None:
        raise ValueError(f"could not read .3dm file at {path}")
    return model


def digest_model(path: Path) -> str:
    """Canonical content digest of a .3dm file.

    Covers every object's name, layer, user strings, bounding box, and
    vertex locations, plus the layer table and archive version — the
    content a hand edit would change. Random per-write UUIDs and archive
    metadata are deliberately excluded (byte equality is unattainable;
    see module docstring).
    """
    model = rhino3dm_read(path)
    archive_version = rhino3dm.File3dm.ReadArchiveVersion(str(path))
    layer_names = [layer.Name for layer in model.Layers]
    objects = []
    for obj in model.Objects:  # type: ignore[attr-defined]  # stub omits __iter__; runtime iterates
        geometry = obj.Geometry
        bbox = geometry.GetBoundingBox()
        attributes = obj.Attributes
        objects.append(
            {
                "name": attributes.Name,
                "layer": layer_names[attributes.LayerIndex],
                "user_strings": dict(sorted(attributes.GetUserStrings())),
                "bbox_min": [_rounded(bbox.Min.X), _rounded(bbox.Min.Y), _rounded(bbox.Min.Z)],
                "bbox_max": [_rounded(bbox.Max.X), _rounded(bbox.Max.Y), _rounded(bbox.Max.Z)],
                "vertices": [
                    [_rounded(v.Location.X), _rounded(v.Location.Y), _rounded(v.Location.Z)]
                    for v in geometry.Vertices
                ],
            }
        )
    payload = {"archive_version": archive_version, "layers": layer_names, "objects": objects}
    canonical = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def check_roundtrip(
    spec: FurnitureSpec, plans: list[PartPlan], config: GenerationConfig, shipped: Path
) -> CheckResult:
    """The shipped .3dm regenerates identically from the approved spec."""
    with tempfile.TemporaryDirectory() as tmp:
        regenerated = Path(tmp) / "regen.3dm"
        model = build_model(spec, plans, config)
        if not model.Write(str(regenerated), config.file3dm_version):
            return CheckResult(
                name="roundtrip_reproducibility",
                status=CheckStatus.FAIL,
                failures=("regeneration could not be written",),
            )
        shipped_digest = digest_model(shipped)
        regenerated_digest = digest_model(regenerated)
    if shipped_digest != regenerated_digest:
        return CheckResult(
            name="roundtrip_reproducibility",
            status=CheckStatus.FAIL,
            failures=(
                "shipped .3dm content digest differs from a fresh regeneration — "
                "the file was modified after generation",
            ),
        )
    return CheckResult(
        name="roundtrip_reproducibility",
        status=CheckStatus.PASS,
        notes=("content-digest comparison (byte equality is unattainable; see verify docstring)",),
    )


def _polyline_points(entity: LWPolyline) -> tuple[tuple[float, float], ...]:
    """An LWPolyline's vertices as plain float pairs."""
    return tuple(
        (_rounded(float(point[0])), _rounded(float(point[1]))) for point in entity.get_points("xy")
    )


def _sheet_frame(msp: BaseLayout, sheet_layer: str) -> tuple[float, float, float, float] | None:
    """The sheet boundary rectangle's (x0, y0, x1, y1), if drawn."""
    frames: list[tuple[float, float, float, float]] = []
    for entity in msp:
        if not (isinstance(entity, LWPolyline) and entity.dxf.layer == sheet_layer):
            continue
        points = _polyline_points(entity)
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        frames.append((min(xs), min(ys), max(xs), max(ys)))
    return max(frames) if frames else None


def check_dxf_sheets(paths: list[Path], config: GenerationConfig, units: Units) -> CheckResult:
    """Fabrication-file checks on every written sheet (blueprint §5, R1 §2).

    Units match the spec, all configured layers exist, CUT/POCKET
    contours are closed with at least three points and no duplicates,
    drill circles have positive radius, and every part outline sits
    inside the sheet's usable area.
    """
    failures: list[str] = []
    expected_units = DXF_INSUNITS[units]
    declared = {name for name, _ in config.layers.fabrication_layers()} | {config.layers.sheet}

    for path in paths:
        doc = readfile(str(path))
        actual_units = doc.header.get("$INSUNITS")
        if actual_units != expected_units:
            failures.append(f"{path.name}: $INSUNITS is {actual_units}, expected {expected_units}")
        layer_names = {layer.dxf.name for layer in doc.layers}
        for name in sorted(declared - layer_names):
            failures.append(f"{path.name}: missing layer '{name}'")

        msp = doc.modelspace()
        frame = _sheet_frame(msp, config.layers.sheet)
        usable: tuple[float, float, float, float] | None = None
        if frame is not None:
            usable = (
                config.sheet_margin,
                config.sheet_margin,
                frame[2] - config.sheet_margin,
                frame[3] - config.sheet_margin,
            )
        outlines: set[tuple[tuple[float, float], ...]] = set()
        for entity in msp:
            layer = entity.dxf.layer
            if layer not in declared:
                failures.append(f"{path.name}: entity on undeclared layer '{layer}'")
                continue
            if isinstance(entity, LWPolyline) and layer in (
                config.layers.cut,
                config.layers.pocket,
            ):
                points = _polyline_points(entity)
                if not entity.closed:
                    failures.append(f"{path.name}: '{layer}' contour is not closed")
                if len(points) < 3:
                    failures.append(f"{path.name}: '{layer}' contour has {len(points)} points")
                if points in outlines:
                    failures.append(f"{path.name}: duplicate '{layer}' contour {points[0]}")
                outlines.add(points)
                if layer == config.layers.cut and usable is not None:
                    xs, ys = [p[0] for p in points], [p[1] for p in points]
                    inside = (
                        min(xs) >= usable[0]
                        and min(ys) >= usable[1]
                        and max(xs) <= usable[2]
                        and max(ys) <= usable[3]
                    )
                    if not inside:
                        failures.append(
                            f"{path.name}: part outline {min(xs):g},{min(ys):g}.."
                            f"{max(xs):g},{max(ys):g} leaves the usable sheet area"
                        )
            elif (
                isinstance(entity, Circle)
                and layer == config.layers.drill
                and entity.dxf.radius <= 0
            ):
                failures.append(f"{path.name}: drill circle with radius {entity.dxf.radius}")
    status = CheckStatus.FAIL if failures else CheckStatus.PASS
    return CheckResult(name="dxf_fabrication_checks", status=status, failures=tuple(failures))
