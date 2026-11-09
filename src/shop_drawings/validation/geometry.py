"""Geometric sanity — blueprint §5: "generated parts fit their declared stock
thickness; hole patterns fit within panel edges; door reveals and clearances
are physically possible."

Every rule here is a necessary physical condition stated from the spec's own
values, so a failure names the impossible numbers rather than a hunch: a
panel thicker than its smallest face, a hole pattern that does not fit
within the face it is cut into, a clear opening with no stock left for its
walls, a reveal larger than the opening it reveals.
"""

from __future__ import annotations

from shop_drawings.schema import (
    Constraint,
    ConstraintType,
    DimensionKind,
    FurnitureSpec,
    Part,
    TracedValue,
)

from .extents import extent_kinds_of, face_extents_of
from .report import RuleId, ValidationIssue, ValueEvidence

_EPSILON = 1e-9

#: Feature dimension kinds that measure how far a hole sits from a reference
#: edge or face. The hole must still land inside the panel beyond that offset.
_OFFSET_KINDS = (DimensionKind.SETBACK, DimensionKind.EDGE_DISTANCE)

_CLEAR_TO_OVERALL = {
    ConstraintType.CLEAR_WIDTH: ConstraintType.OVERALL_WIDTH,
    ConstraintType.CLEAR_HEIGHT: ConstraintType.OVERALL_HEIGHT,
    ConstraintType.CLEAR_DEPTH: ConstraintType.OVERALL_DEPTH,
}


def _evidence(
    location: str, role: str, value: TracedValue, detail: str | None = None
) -> ValueEvidence:
    return ValueEvidence(
        location=location, role=role, value=value.value, source=value.source, detail=detail
    )


def _dimension_evidence(
    part: Part, kind: DimensionKind, detail: str | None = None
) -> ValueEvidence:
    return _evidence(f"part '{part.id}'", kind.value, part.dimensions[kind], detail)


def _check_thickness_fits(part: Part, issues: list[ValidationIssue]) -> None:
    """A panel cannot be thicker than its own smallest face extent."""
    if DimensionKind.THICKNESS not in part.dimensions:
        return
    faces = face_extents_of(part)
    if not faces:
        return
    thickness = part.dimensions[DimensionKind.THICKNESS].value
    smallest_face = min(faces)
    if thickness > smallest_face + _EPSILON:
        issues.append(
            ValidationIssue(
                rule=RuleId.GEOMETRIC_SANITY,
                message=(
                    f"part '{part.id}' is {thickness:g} mm thick but its smallest declared "
                    f"face extent is {smallest_face:g} mm — a panel cannot be thicker "
                    "than its face"
                ),
                evidence=[
                    _dimension_evidence(part, DimensionKind.THICKNESS),
                    *[
                        _dimension_evidence(part, kind, detail="face extent")
                        for kind in extent_kinds_of(part)
                        if kind is not DimensionKind.THICKNESS
                    ],
                ],
            )
        )


def _hole_diameter_of(part: Part, feature_index: int) -> float | None:
    feature = part.features[feature_index]
    dimension = feature.dimensions.get(DimensionKind.HOLE_DIAMETER)
    return dimension.value if dimension else None


def _check_feature_fits(part: Part, feature_index: int, issues: list[ValidationIssue]) -> None:
    """Holes must fit within the panel face and not overlap each other."""
    feature = part.features[feature_index]
    where = f"part '{part.id}' feature[{feature_index}] ({feature.type.value})"
    diameter = _hole_diameter_of(part, feature_index)
    if diameter is None:
        return
    faces = face_extents_of(part)
    if faces and diameter > min(faces) + _EPSILON:
        issues.append(
            ValidationIssue(
                rule=RuleId.GEOMETRIC_SANITY,
                message=(
                    f"{where} has a {diameter:g} mm hole that cannot fit within the "
                    f"panel's smallest face extent ({min(faces):g} mm)"
                ),
                evidence=[
                    _evidence(
                        where,
                        DimensionKind.HOLE_DIAMETER.value,
                        feature.dimensions[DimensionKind.HOLE_DIAMETER],
                    ),
                    *[
                        _dimension_evidence(part, kind, detail="face extent")
                        for kind in extent_kinds_of(part)
                        if kind is not DimensionKind.THICKNESS
                    ],
                ],
            )
        )
    spacing = feature.dimensions.get(DimensionKind.SPACING)
    if spacing and diameter >= spacing.value - _EPSILON:
        issues.append(
            ValidationIssue(
                rule=RuleId.GEOMETRIC_SANITY,
                message=(
                    f"{where} has {diameter:g} mm holes on {spacing.value:g} mm centers — "
                    "the holes touch or overlap"
                ),
                evidence=[
                    _evidence(
                        where,
                        DimensionKind.HOLE_DIAMETER.value,
                        feature.dimensions[DimensionKind.HOLE_DIAMETER],
                    ),
                    _evidence(where, DimensionKind.SPACING.value, spacing),
                ],
            )
        )
    for offset_kind in _OFFSET_KINDS:
        offset = feature.dimensions.get(offset_kind)
        if not offset or not faces:
            continue
        farthest_face = max(faces)
        if offset.value + diameter / 2 > farthest_face + _EPSILON:
            issues.append(
                ValidationIssue(
                    rule=RuleId.GEOMETRIC_SANITY,
                    message=(
                        f"{where} places a {diameter:g} mm hole {offset.value:g} mm from its "
                        f"reference, but the panel's largest face extent is only "
                        f"{farthest_face:g} mm — the hole breaks out of the edge"
                    ),
                    evidence=[
                        _evidence(where, offset_kind.value, offset),
                        _evidence(
                            where,
                            DimensionKind.HOLE_DIAMETER.value,
                            feature.dimensions[DimensionKind.HOLE_DIAMETER],
                        ),
                    ],
                )
            )


def _constraint_by_type(spec: FurnitureSpec, kind: ConstraintType) -> list[Constraint]:
    return [constraint for constraint in spec.constraints if constraint.type is kind]


def _check_clearance(spec: FurnitureSpec, clear: Constraint, issues: list[ValidationIssue]) -> None:
    """A clear opening must fit inside its overall, leaving stock for the walls."""
    overall_type = _CLEAR_TO_OVERALL[clear.type]
    overalls = _constraint_by_type(spec, overall_type)
    if not overalls:
        return
    overall = overalls[0]
    if clear.value > overall.value + clear.tolerance + overall.tolerance + _EPSILON:
        issues.append(
            ValidationIssue(
                rule=RuleId.GEOMETRIC_SANITY,
                message=(
                    f"constraint '{clear.type.value}' is {clear.value:g} mm but "
                    f"'{overall_type.value}' is only {overall.value:g} mm — the inside "
                    "cannot be larger than the outside"
                ),
                evidence=[
                    _evidence(f"constraint '{clear.type.value}'", clear.type.value, clear),
                    _evidence(f"constraint '{overall_type.value}'", overall_type.value, overall),
                ],
            )
        )
        return
    thicknesses = [
        part.dimensions[DimensionKind.THICKNESS].value
        for part in spec.parts
        if DimensionKind.THICKNESS in part.dimensions
    ]
    if not thicknesses:
        return
    thinnest_wall = min(thicknesses)
    walls = 2 * thinnest_wall
    if clear.value + walls > overall.value + clear.tolerance + overall.tolerance + _EPSILON:
        issues.append(
            ValidationIssue(
                rule=RuleId.GEOMETRIC_SANITY,
                message=(
                    f"constraint '{clear.type.value}' is {clear.value:g} mm and "
                    f"'{overall_type.value}' is {overall.value:g} mm — with the thinnest "
                    f"declared wall at {thinnest_wall:g} mm, two walls need "
                    f"{walls:g} mm and leave no stock"
                ),
                evidence=[
                    _evidence(f"constraint '{clear.type.value}'", clear.type.value, clear),
                    _evidence(f"constraint '{overall_type.value}'", overall_type.value, overall),
                    *[
                        _dimension_evidence(part, DimensionKind.THICKNESS, detail="wall stock")
                        for part in spec.parts
                        if part.dimensions.get(DimensionKind.THICKNESS, None)
                        and part.dimensions[DimensionKind.THICKNESS].value == thinnest_wall
                    ],
                ],
            )
        )


def _bounds_for_reveal(spec: FurnitureSpec, part: Part) -> list[float]:
    """Everything a reveal on ``part`` must be smaller than, when known."""
    bounds = [
        constraint.value
        for constraint in spec.constraints
        if constraint.type in _CLEAR_TO_OVERALL
        or constraint.type in set(_CLEAR_TO_OVERALL.values())
    ]
    return bounds + face_extents_of(part)


def _check_reveals(spec: FurnitureSpec, issues: list[ValidationIssue]) -> None:
    for part in spec.parts:
        reveal_locations: list[tuple[str, TracedValue]] = (
            [(f"part '{part.id}'", part.dimensions[DimensionKind.REVEAL])]
            if DimensionKind.REVEAL in part.dimensions
            else []
        )
        for feature_index, feature in enumerate(part.features):
            if DimensionKind.REVEAL in feature.dimensions:
                reveal_locations.append(
                    (
                        f"part '{part.id}' feature[{feature_index}] ({feature.type.value})",
                        feature.dimensions[DimensionKind.REVEAL],
                    )
                )
        bounds = _bounds_for_reveal(spec, part)
        if not bounds:
            continue
        smallest_bound = min(bounds)
        for where, reveal in reveal_locations:
            if reveal.value >= smallest_bound - _EPSILON:
                issues.append(
                    ValidationIssue(
                        rule=RuleId.GEOMETRIC_SANITY,
                        message=(
                            f"{where} declares a {reveal.value:g} mm reveal, but the smallest "
                            f"opening or face it could belong to is {smallest_bound:g} mm — "
                            "the reveal cannot be larger than what it reveals"
                        ),
                        evidence=[
                            _evidence(where, DimensionKind.REVEAL.value, reveal),
                        ],
                    )
                )


def check_geometry(spec: FurnitureSpec) -> list[ValidationIssue]:
    """Physical-possibility checks over parts, features, and clear constraints."""
    issues: list[ValidationIssue] = []
    for part in spec.parts:
        _check_thickness_fits(part, issues)
        for feature_index in range(len(part.features)):
            _check_feature_fits(part, feature_index, issues)
    for constraint in spec.constraints:
        if constraint.type in _CLEAR_TO_OVERALL:
            _check_clearance(spec, constraint, issues)
    _check_reveals(spec, issues)
    return issues
