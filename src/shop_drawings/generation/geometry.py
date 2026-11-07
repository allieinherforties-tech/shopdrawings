"""The v1 panel-and-case primitive library.

Resolves an approved :class:`~shop_drawings.schema.Part` into a
:class:`PartPlan` — an exact outline, thickness, and machined features in
part-local coordinates. Resolution is pure and deterministic: same spec in,
same plan out, no AI, no inference. A part the library cannot resolve
raises :class:`UnresolvablePartError` carrying every reason, so the
pipeline can emit a complete escalation package instead of fabricated
geometry (blueprint §3).

Conventions of the v1 library (deterministic and documented, so the
blueprint's ban on silent inference holds — anything not covered here is
an escalation, never a guess):

- **Outline axes.** A part's extent dimensions are taken in the schema's
  ``DimensionKind`` definition order (length, width, depth, height): first
  becomes the outline X (the *run axis* along which channels and hole
  runs extend), second becomes the outline Y (the *offset axis*). For a
  panel, thickness is the Z extent; for a solid, the third extent is Z.
- **Reference edges.** ``setback``/``edge_distance`` values with a
  ``reference`` name are measured from that edge: front/left measure from
  the zero end of the offset/run axis, back/right from the far end. No
  reference means the zero end. Any other reference is an escalation.
- **Shelf-pin runs.** Holes run along the X axis on one Y column. The
  first hole sits ``edge_distance`` from the run origin, defaulting to
  one on-center spacing (the run is inset a full spacing from each end of
  the panel). A run that cannot place at least one hole escalates.
- **Channels (dado, groove, rebate).** The channel runs along X; its
  centerline sits ``setback`` from the referenced edge of the Y axis, and
  it runs the full panel unless the feature states ``edge_distance``
  (run start) or ``length`` (stopped run). Cut depth is measured from
  the +Z face; orienting parts within the assembly is out of v1 scope.
- **Mortise.** A fully positioned stopped pocket: requires width, depth,
  setback, edge_distance, and length. A tenon is an added volume whose
  orientation the schema's dimension keys cannot yet express — parts
  with tenons escalate rather than get guessed geometry.
- **Hardware.** Not fabricated: no geometry, no cut-sheet profile; it
  appears in the cut list only. A hardware part carrying features
  escalates rather than silently dropping them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from shop_drawings.generation.errors import UnresolvablePartError
from shop_drawings.schema import (
    Dimension,
    DimensionKind,
    Edge,
    Feature,
    FeatureType,
    Part,
    PartType,
)

#: Extent kinds in the order the outline axes are assigned (X, Y, then Z
#: for solids). Definition order, not alphabetical — see module docstring.
_EXTENT_ORDER: tuple[DimensionKind, ...] = (
    DimensionKind.LENGTH,
    DimensionKind.WIDTH,
    DimensionKind.DEPTH,
    DimensionKind.HEIGHT,
)

#: Feature types the v1 library resolves. ``TENON`` is deliberately absent.
_RESOLVABLE_FEATURES: frozenset[FeatureType] = frozenset(
    {
        FeatureType.HOLE,
        FeatureType.SHELF_PIN_HOLES,
        FeatureType.POCKET_HOLE,
        FeatureType.DADO,
        FeatureType.GROOVE,
        FeatureType.REBATE,
        FeatureType.MORTISE,
    }
)

_DRILL_FEATURES: frozenset[FeatureType] = frozenset(
    {FeatureType.HOLE, FeatureType.SHELF_PIN_HOLES, FeatureType.POCKET_HOLE}
)

_CHANNEL_FEATURES: frozenset[FeatureType] = frozenset(
    {FeatureType.DADO, FeatureType.GROOVE, FeatureType.REBATE}
)


@dataclass(frozen=True)
class Drill:
    """A through-thickness hole, centered in part-local coordinates."""

    center_x: float
    center_y: float
    diameter: float


@dataclass(frozen=True)
class Pocket:
    """A rectangular machined recess: cut depth measured from the +Z face."""

    x: float
    y: float
    length: float
    width: float
    depth: float


@dataclass(frozen=True)
class PartPlan:
    """Resolved fabrication geometry for one part, in part-local units.

    The part occupies ``(0, 0)`` to ``(length, width)`` in plan view with
    thickness along +Z. Features are positioned in that frame; writers
    (DXF, .3dm) place copies wherever layout demands.
    """

    part_id: str
    part_type: PartType
    material: str
    quantity: int
    length: float  # outline X extent
    width: float  # outline Y extent
    thickness: float  # Z extent
    edge_banding: tuple[Edge, ...]
    drills: tuple[Drill, ...]
    pockets: tuple[Pocket, ...]

    def fits_inside(self, sheet_length: float, sheet_width: float) -> bool:
        """Whether the outline fits a sheet of the given size, either orientation."""
        return (self.length <= sheet_length and self.width <= sheet_width) or (
            self.length <= sheet_width and self.width <= sheet_length
        )


def extents_in_order(part: Part) -> list[tuple[DimensionKind, Dimension]]:
    """The part's extent dimensions, in outline-axis assignment order."""
    return [(kind, part.dimensions[kind]) for kind in _EXTENT_ORDER if kind in part.dimensions]


def _reference_offset(reference: str | None, value: float, extent: float) -> float | None:
    """Distance from the zero end of an axis for a named reference edge.

    ``None`` means the zero end by convention. ``None`` return means the
    reference is not in the v1 vocabulary — an escalation, not a guess.
    """
    if reference is None:
        return value
    name = reference.strip().lower()
    if any(word in name for word in ("front", "left")):
        return value
    if any(word in name for word in ("back", "right")):
        return extent - value
    return None


def _resolve_drill(
    feature: Feature, plan_length: float, plan_width: float, reasons: list[str]
) -> Drill | None:
    """Resolve a single positioned hole (``hole``/``pocket_hole``)."""
    diameter = feature.dimensions.get(DimensionKind.HOLE_DIAMETER)
    setback = feature.dimensions.get(DimensionKind.SETBACK)
    edge_distance = feature.dimensions.get(DimensionKind.EDGE_DISTANCE)
    if diameter is None or setback is None or edge_distance is None:
        missing = [
            kind.value
            for kind, dim in (
                (DimensionKind.HOLE_DIAMETER, diameter),
                (DimensionKind.SETBACK, setback),
                (DimensionKind.EDGE_DISTANCE, edge_distance),
            )
            if dim is None
        ]
        reasons.append(
            f"{feature.type.value} needs hole_diameter, setback and edge_distance "
            f"to position a single hole (missing: {', '.join(missing)})"
        )
        return None
    offset_y = _reference_offset(setback.reference, setback.value, plan_width)
    offset_x = _reference_offset(edge_distance.reference, edge_distance.value, plan_length)
    if offset_y is None:
        reasons.append(f"setback reference '{setback.reference}' is not in the v1 vocabulary")
    if offset_x is None:
        reasons.append(
            f"edge_distance reference '{edge_distance.reference}' is not in the v1 vocabulary"
        )
    if offset_x is None or offset_y is None:
        return None
    return Drill(center_x=offset_x, center_y=offset_y, diameter=diameter.value)


def _resolve_pin_run(
    feature: Feature, plan_length: float, plan_width: float, reasons: list[str]
) -> list[Drill]:
    """Resolve a shelf-pin hole run along the X axis.

    The column sits ``setback`` from the referenced Y edge (front edge by
    default); holes start ``edge_distance`` from the run origin — one
    on-center spacing by default — and continue while the next hole stays
    at least one spacing from the far end of the run axis.
    """
    diameter = feature.dimensions.get(DimensionKind.HOLE_DIAMETER)
    spacing = feature.dimensions.get(DimensionKind.SPACING)
    setback = feature.dimensions.get(DimensionKind.SETBACK)
    if diameter is None or spacing is None or setback is None:
        missing = [
            kind.value
            for kind, dim in (
                (DimensionKind.HOLE_DIAMETER, diameter),
                (DimensionKind.SPACING, spacing),
                (DimensionKind.SETBACK, setback),
            )
            if dim is None
        ]
        reasons.append(
            f"shelf_pin_holes needs hole_diameter, spacing and setback (missing: "
            f"{', '.join(missing)})"
        )
        return []
    offset_y = _reference_offset(setback.reference, setback.value, plan_width)
    if offset_y is None:
        reasons.append(f"setback reference '{setback.reference}' is not in the v1 vocabulary")
        return []
    if spacing.value <= 0:
        reasons.append(f"shelf_pin_holes spacing must be positive, got {spacing.value}")
        return []

    edge_distance = feature.dimensions.get(DimensionKind.EDGE_DISTANCE)
    start = spacing.value
    if edge_distance is not None:
        offset_x_start = _reference_offset(
            edge_distance.reference, edge_distance.value, plan_length
        )
        if offset_x_start is None:
            reasons.append(
                f"edge_distance reference '{edge_distance.reference}' is not in the v1 vocabulary"
            )
            return []
        start = offset_x_start

    run_positions = [
        start + index * spacing.value
        for index in range(int((plan_length - spacing.value - start) // spacing.value) + 1)
        if plan_length - start >= 0
    ]
    if not run_positions:
        reasons.append(
            f"panel run of {plan_length:g} cannot hold a shelf-pin run starting at {start:g} "
            f"with spacing {spacing.value:g} — too short for one inset hole at each end"
        )
        return []
    return [Drill(center_x=x, center_y=offset_y, diameter=diameter.value) for x in run_positions]


def _resolve_channel(
    feature: Feature, plan_length: float, plan_width: float, reasons: list[str]
) -> Pocket | None:
    """Resolve a rectangular channel or recess (dado, groove, rebate, mortise).

    A mortise is a stopped, fully positioned recess and must state
    ``edge_distance`` and ``length``; channels may omit both and default
    to a full-length run from the zero end of the run axis.
    """
    width = feature.dimensions.get(DimensionKind.WIDTH)
    depth = feature.dimensions.get(DimensionKind.DEPTH)
    setback = feature.dimensions.get(DimensionKind.SETBACK)
    edge_distance = feature.dimensions.get(DimensionKind.EDGE_DISTANCE)
    run_length = feature.dimensions.get(DimensionKind.LENGTH)
    if width is None or depth is None or setback is None:
        missing = [
            kind.value
            for kind, dim in (
                (DimensionKind.WIDTH, width),
                (DimensionKind.DEPTH, depth),
                (DimensionKind.SETBACK, setback),
            )
            if dim is None
        ]
        reasons.append(
            f"{feature.type.value} needs width, depth and setback (missing: {', '.join(missing)})"
        )
        return None
    if feature.type is FeatureType.MORTISE and (edge_distance is None or run_length is None):
        reasons.append(
            "mortise is a stopped recess and needs edge_distance and length as well as "
            "width, depth and setback"
        )
        return None

    center_y = _reference_offset(setback.reference, setback.value, plan_width)
    if center_y is None:
        reasons.append(f"setback reference '{setback.reference}' is not in the v1 vocabulary")
        return None
    start = 0.0
    if edge_distance is not None:
        offset_x = _reference_offset(edge_distance.reference, edge_distance.value, plan_length)
        if offset_x is None:
            reasons.append(
                f"edge_distance reference '{edge_distance.reference}' is not in the v1 vocabulary"
            )
            return None
        start = offset_x
    length = run_length.value if run_length is not None else plan_length - start
    return Pocket(
        x=start,
        y=center_y - width.value / 2,
        length=length,
        width=width.value,
        depth=depth.value,
    )


def _resolve_features(
    part: Part, plan_length: float, plan_width: float
) -> tuple[list[Drill], list[Pocket], list[str]]:
    """Resolve every feature, collecting escalation reasons as they arise."""
    drills: list[Drill] = []
    pockets: list[Pocket] = []
    reasons: list[str] = []
    for feature in part.features:
        if feature.type not in _RESOLVABLE_FEATURES:
            reasons.append(f"feature '{feature.type.value}' is outside the v1 primitive library")
            continue
        if feature.type in _DRILL_FEATURES:
            if feature.type is FeatureType.SHELF_PIN_HOLES:
                drills.extend(_resolve_pin_run(feature, plan_length, plan_width, reasons))
            else:
                drill = _resolve_drill(feature, plan_length, plan_width, reasons)
                if drill is not None:
                    drills.append(drill)
        elif feature.type in _CHANNEL_FEATURES or feature.type is FeatureType.MORTISE:
            pocket = _resolve_channel(feature, plan_length, plan_width, reasons)
            if pocket is not None:
                pockets.append(pocket)
    return drills, pockets, reasons


def resolve_part(part: Part) -> PartPlan:
    """Resolve an approved part into exact fabrication geometry.

    Raises :class:`UnresolvablePartError` with the blocking reasons when the
    v1 library cannot resolve the part (all feature problems at once, so
    one escalation answers everything); the pipeline routes those parts to
    escalation packages.
    """
    reasons: list[str] = []
    if part.type is PartType.HARDWARE:
        if part.features:
            reasons.append("hardware carries features, which would be silently dropped")
        if reasons:
            raise UnresolvablePartError(part.id, reasons)
        return PartPlan(
            part_id=part.id,
            part_type=part.type,
            material=part.material,
            quantity=part.quantity,
            length=0.0,
            width=0.0,
            thickness=0.0,
            edge_banding=tuple(part.edge_banding),
            drills=(),
            pockets=(),
        )

    thickness = part.dimensions.get(DimensionKind.THICKNESS)
    extents = extents_in_order(part)
    ordered_extents = [dim.value for _, dim in extents]
    expected_extents = 2 if part.type is PartType.PANEL else 3
    if len(extents) != expected_extents:
        stated = ", ".join(sorted(kind.value for kind, _ in extents))
        raise UnresolvablePartError(
            part.id,
            [
                f"a {part.type.value} needs exactly {expected_extents} extent dimension(s) "
                f"(length/width/depth/height), got {len(extents)}: {stated or 'none'}"
            ],
        )
    plan_thickness: float
    if part.type is PartType.PANEL:
        if thickness is None:
            raise UnresolvablePartError(
                part.id,
                ["no thickness dimension — material thickness must be stated, not inferred"],
            )
        plan_thickness = thickness.value
    else:  # solid: the third extent is the Z (thickness) dimension
        plan_thickness = ordered_extents[2]
        if thickness is not None and abs(thickness.value - plan_thickness) > 1e-9:
            raise UnresolvablePartError(
                part.id,
                [
                    f"solid declares thickness {thickness.value:g} but its third extent is "
                    f"{plan_thickness:g} — contradictory dimensions"
                ],
            )
    if plan_thickness <= 0:
        raise UnresolvablePartError(part.id, [f"thickness must be positive, got {plan_thickness}"])

    plan_length, plan_width = ordered_extents[0], ordered_extents[1]
    drills, pockets, feature_reasons = _resolve_features(part, plan_length, plan_width)
    if feature_reasons:
        raise UnresolvablePartError(part.id, feature_reasons)
    return PartPlan(
        part_id=part.id,
        part_type=part.type,
        material=part.material,
        quantity=part.quantity,
        length=plan_length,
        width=plan_width,
        thickness=plan_thickness,
        edge_banding=tuple(part.edge_banding),
        drills=tuple(drills),
        pockets=tuple(pockets),
    )


def summarize_features(drills: Iterable[Drill], pockets: Iterable[Pocket]) -> str:
    """One-line machining summary for cut lists and packages."""
    drill_list, pocket_list = list(drills), list(pockets)
    parts = []
    if drill_list:
        diameters = sorted({drill.diameter for drill in drill_list})
        shown = ", ".join(f"{d:g}" for d in diameters)
        parts.append(f"{len(drill_list)} drill ⌀{shown}")
    if pocket_list:
        depths = sorted({pocket.depth for pocket in pocket_list})
        shown = ", ".join(f"{d:g}" for d in depths)
        parts.append(f"{len(pocket_list)} pocket depth {shown}")
    return "; ".join(parts)
