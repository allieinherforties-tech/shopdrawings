"""Reconciliation: the assembly becomes a schema-valid ``FurnitureSpec``.

The output is always a *draft*: every value ``needs_review``, the spec never
``approved`` (the gate forbids constructing an approved spec with open
questions, and extraction always has open questions). Anything the sheet did
not state — a material, a unit, the meaning of a number — lands in
``unresolved_questions`` rather than being assumed (blueprint §4: never infer
silently).
"""

from __future__ import annotations

from typing import Final

from shop_drawings.schema import (
    Constraint,
    ConstraintType,
    Dimension,
    DimensionKind,
    Edge,
    Feature,
    FeatureType,
    FurnitureSpec,
    Part,
    PartType,
    Project,
    ReviewStatus,
    SourceReference,
    Units,
)

from .associate import REVIEW_CONFIDENCE_FLOOR, Assembly, AttachedDimension, PartCallout
from .geometry import Region
from .models import TextBlock, TitleKV, Unassociated, UnassociatedKind

#: Material name recorded when the sheet states none — never a guessed stock.
_UNKNOWN_MATERIAL: Final = "unspecified"

_UNIT_ALIASES: Final = {
    "MM": Units.MM,
    "MILLIMETRE": Units.MM,
    "MILLIMETERS": Units.MM,
    "MILLIMETRES": Units.MM,
    "CM": Units.CM,
    "CENTIMETRE": Units.CM,
    "CENTIMETRES": Units.CM,
    "IN": Units.INCH,
    "INCH": Units.INCH,
    "INCHES": Units.INCH,
}


def _source(block: TextBlock) -> SourceReference:
    """A block becomes the schema's source reference (padded crop + text)."""
    region: Region = block.region.padded()
    return SourceReference(page=block.page, region=region.as_tuple(), text=block.text)


def _question(unassociated: Unassociated) -> str:
    where = (
        f"page {unassociated.block.page}, region "
        f"[{unassociated.block.region.x0:.2f}, {unassociated.block.region.y0:.2f}, "
        f"{unassociated.block.region.x1:.2f}, {unassociated.block.region.y1:.2f}]"
    )
    if unassociated.kind is UnassociatedKind.BARE_NUMBER:
        return f"Unassigned value {unassociated.detail} — at {where}. What does it dimension?"
    return f"{unassociated.detail} — at {where}. Please resolve."


def _first_kv(title_block: tuple[TitleKV, ...], *keys: str) -> str | None:
    for kv in title_block:
        if kv.key in keys:
            return kv.value
    return None


def _build_project(assembly: Assembly, source_name: str, questions: list[str]) -> Project:
    name = _first_kv(assembly.title_block, "PROJECT", "JOB", "CLIENT")
    if name is None:
        name = source_name.rsplit(".", 1)[0]
        questions.append(
            f"Title block states no project name; using the file name {name!r}. Confirm."
        )
    units_text = _first_kv(assembly.title_block, "UNITS")
    units = None
    if units_text is not None:
        units = _UNIT_ALIASES.get(units_text.strip().upper())
        if units is None:
            questions.append(
                f"Title block unit {units_text!r} is not one of mm/cm/in; "
                "treating as millimetres. Confirm."
            )
    if units is None:
        units = Units.MM
        questions.append(
            "Title block states no units; defaulting to millimetres "
            "(blueprint §6 working assumption). Confirm."
        )
    return Project(name=name, units=units, source=source_name)


def _dimension(kind: DimensionKind, attached: AttachedDimension) -> Dimension:
    return Dimension(
        value=attached.dimension.value,
        source=_source(attached.dimension.block),
        confidence=attached.confidence,
        reference=attached.dimension.reference,
    )


def _size_dimensions(callout: PartCallout, questions: list[str]) -> dict[DimensionKind, Dimension]:
    """Ordered size span → typed dimensions, cross-checked against the material."""
    if callout.sizes_block is None:
        return {}
    source = _source(callout.sizes_block)
    base = callout.sizes_confidence
    values = callout.sizes

    if len(values) == 3:
        kinds: tuple[DimensionKind, ...] = (
            DimensionKind.LENGTH,
            DimensionKind.WIDTH,
            DimensionKind.THICKNESS,
        )
        if (
            callout.material_thickness is not None
            and abs(values[2] - callout.material_thickness) > 0.01
        ):
            questions.append(
                f"Part {callout.part_id!r}: size-line thickness {values[2]:g} does not match "
                f"declared material thickness {callout.material_thickness:g}. Confirm the stock."
            )
    elif len(values) == 2:
        kinds = (DimensionKind.LENGTH, DimensionKind.WIDTH)
        questions.append(
            f"Part {callout.part_id!r}: two-value size line interpreted as length × width — "
            "confirm the interpretation."
        )
    else:
        questions.append(
            f"Part {callout.part_id!r}: size line with {len(values)} values cannot be typed; "
            "state each dimension's meaning."
        )
        return {}

    dims: dict[DimensionKind, Dimension] = {
        kind: Dimension(value=value, source=source, confidence=base)
        for kind, value in zip(kinds, values, strict=True)
    }
    if len(values) == 2:
        # The size line did not state a thickness; the material line did.
        material_dim = _material_thickness_dimension(callout)
        if material_dim is not None and material_dim[0] not in dims:
            dims[material_dim[0]] = material_dim[1]
    return dims


def _material_thickness_dimension(
    callout: PartCallout,
) -> tuple[DimensionKind, Dimension] | None:
    """The stock thickness the material line states, if the size line did not.

    A material line like ``12mm birch plywood`` states the stock thickness as
    a traced fact — recording it is not inference; the size-line question
    above still stands on its own terms.
    """
    if (
        callout.material_block is None
        or callout.material_thickness is None
        or callout.material_confidence < REVIEW_CONFIDENCE_FLOOR
    ):
        return None
    return DimensionKind.THICKNESS, Dimension(
        value=callout.material_thickness,
        source=_source(callout.material_block),
        confidence=callout.material_confidence,
    )


def _attached_features(callout: PartCallout) -> list[Feature]:
    """Dimension groups from one line become one feature (e.g. shelf-pin holes)."""
    features: list[Feature] = []
    by_block: dict[int, list[AttachedDimension]] = {}
    for attached in callout.dimensions:
        by_block.setdefault(id(attached.dimension.block), []).append(attached)
    for group in by_block.values():
        kinds = {d.dimension.kind for d in group}
        if not kinds & {"hole_diameter", "spacing"}:
            continue  # plain dimensions, not a feature — handled by the part
        dims: dict[DimensionKind, Dimension] = {}
        for attached in group:
            kind = DimensionKind(attached.dimension.kind)
            dims[kind] = _dimension(kind, attached)
        features.append(Feature(type=FeatureType.SHELF_PIN_HOLES, dimensions=dims))
    return features


def _build_part(callout: PartCallout, questions: list[str]) -> Part:
    dimensions = _size_dimensions(callout, questions)
    if callout.material is None:
        questions.append(
            f"Part {callout.part_id!r} states no material; recorded as {_UNKNOWN_MATERIAL!r}."
        )
    for attached in callout.dimensions:
        kind_text = attached.dimension.kind
        if kind_text in ("hole_diameter", "spacing"):
            continue  # feature dimensions; handled by _attached_features
        kind = DimensionKind(kind_text)
        if kind in dimensions:
            continue  # first claim wins; conflicts surface in review, not by overwrite
        dimensions[kind] = _dimension(kind, attached)

    features = _attached_features(callout)
    for feature in callout.features:
        # Feature carries no source list in the schema; the note's trace
        # lives in the part's context references below.
        features.append(
            Feature(
                type=FeatureType(feature.feature.type),
                note=feature.feature.note,
            )
        )

    context = [callout.label_block]
    if callout.material_block is not None:
        context.append(callout.material_block)
    for attached_feature in callout.features:
        context.append(attached_feature.feature.block)

    return Part(
        id=callout.part_id,
        type=PartType.PANEL,
        quantity=callout.quantity,
        material=callout.material or _UNKNOWN_MATERIAL,
        dimensions=dimensions,
        edge_banding=[Edge(edge) for edge in callout.edges],
        features=features,
        source_references=[_source(block) for block in context],
    )


def _build_constraints(assembly: Assembly, questions: list[str]) -> list[Constraint]:
    """First claim per constraint type wins; disagreeing claims both stay."""
    by_type: dict[str, Constraint] = {}
    for parsed in assembly.constraints:
        constraint = Constraint(
            type=ConstraintType(parsed.type),
            value=parsed.value,
            source=_source(parsed.block),
            confidence=parsed.confidence,
        )
        existing = by_type.get(parsed.type)
        if existing is None:
            by_type[parsed.type] = constraint
        elif abs(existing.value - parsed.value) > 0.01:
            questions.append(
                f"Two values claim {parsed.type} on the sheet: {existing.value:g} and "
                f"{parsed.value:g}. Reconcile the conflict."
            )
    return list(by_type.values())


def build_spec(assembly: Assembly, source_name: str) -> FurnitureSpec:
    """Reconcile an assembly into a draft spec, questions attached."""
    questions: list[str] = []
    project = _build_project(assembly, source_name, questions)
    parts = [_build_part(callout, questions) for callout in assembly.parts]
    constraints = _build_constraints(assembly, questions)
    questions.extend(_question(item) for item in assembly.unassociated)
    return FurnitureSpec(
        project=project,
        parts=parts,
        constraints=constraints,
        unresolved_questions=questions,
        status=ReviewStatus.NEEDS_REVIEW,
    )
