"""Constraint closure — blueprint §5: "summed part dimensions equal stated
overall dimensions within tolerance."

The v1 schema carries no assembly structure (no positions, no orientations),
so a spec cannot say *which* face of a part runs along which axis. What
closure can still guarantee is the necessary condition the blueprint's
failure table names: some selection of the parts' extent dimensions — one
extent per part instance, quantities multiplied in — must sum to each
``overall_*`` constraint within its tolerance. If nothing sums to the
overall, the spec cannot close and the reviewer sees both values: the stated
overall and the closest achievable sum, with the parts that made it.

This is deliberately a necessary, not sufficient, condition. A coincidental
subset can only be ruled out once extraction records assembly structure;
the review gate exists for exactly that residual.
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import groupby

from shop_drawings.schema import (
    Constraint,
    ConstraintType,
    DimensionKind,
    FurnitureSpec,
    Part,
)

from .extents import extent_kinds_of, extent_value
from .report import RuleId, ValidationIssue, ValueEvidence

#: Guard against pathological quantities; furniture quantities are single digits.
MAX_INSTANCES_PER_PART = 200

#: Guard for float comparison against a tolerance of zero.
EPSILON = 1e-9

_OVERALL_TYPES = {
    ConstraintType.OVERALL_WIDTH,
    ConstraintType.OVERALL_HEIGHT,
    ConstraintType.OVERALL_DEPTH,
}


def _instance_candidates(part: Part) -> Iterator[dict[float, DimensionKind]]:
    """One candidate map per part instance: extent value -> the kind it came from."""
    candidates: dict[float, DimensionKind] = {}
    for kind in extent_kinds_of(part):
        candidates.setdefault(extent_value(part, kind), kind)
    for _ in range(min(part.quantity, MAX_INSTANCES_PER_PART)):
        yield candidates


def _closest_sum(
    spec: FurnitureSpec, constraint: Constraint
) -> tuple[float, list[ValueEvidence]] | None:
    """The sum nearest the constraint that part extents can make, and its evidence.

    Returns ``None`` when the spec has no part extents to sum at all.
    A selection is a choice of one extent per chosen part instance — the empty
    selection (sum 0) is never returned, because "no parts make up the
    overall" is a failure, not a closure.
    """
    instance_maps = list(_instance_candidates_map(spec))
    if not instance_maps:
        return None

    #: reachable[sum] -> selection, a tuple of (part id, kind, value) steps.
    reachable: dict[float, tuple[tuple[str, DimensionKind, float], ...]] = {0.0: ()}
    for part_id, candidates in instance_maps:
        layered: dict[float, tuple[tuple[str, DimensionKind, float], ...]] = {}
        for total, selection in reachable.items():
            for value, kind in candidates.items():
                summed = round(total + value, 6)
                if summed not in reachable and summed not in layered:
                    layered[summed] = (*selection, (part_id, kind, value))
        reachable.update(layered)

    usable = {total: sel for total, sel in reachable.items() if sel}
    if not usable:
        return None
    target = constraint.value
    best_total = min(usable, key=lambda total: abs(total - target))
    evidence = [
        ValueEvidence(
            location=f"part '{part_id}'",
            role=kind.value,
            value=value,
            detail="contributes to the closest achievable sum",
        )
        for part_id, kind, value in usable[best_total]
    ]
    return best_total, evidence


def _instance_candidates_map(
    spec: FurnitureSpec,
) -> list[tuple[str, dict[float, DimensionKind]]]:
    """One (part id, candidate map) per part instance in the spec."""
    return [
        (part.id, candidates) for part in spec.parts for candidates in _instance_candidates(part)
    ]


def _closure_issue(constraint: Constraint, spec: FurnitureSpec) -> ValidationIssue | None:
    """One closure check: can the parts make the overall within tolerance?"""
    parts_with_extents = [part for part in spec.parts if extent_kinds_of(part)]
    if not parts_with_extents:
        return ValidationIssue(
            rule=RuleId.CONSTRAINT_CLOSURE,
            message=(
                f"constraint '{constraint.type.value}' is {constraint.value:g} mm ±"
                f"{constraint.tolerance:g} mm but no part carries extent dimensions — "
                "closure cannot be checked, so it cannot be trusted"
            ),
            evidence=[
                ValueEvidence(
                    location=f"constraint '{constraint.type.value}'",
                    role=constraint.type.value,
                    value=constraint.value,
                    source=constraint.source,
                )
            ],
        )

    closest = _closest_sum(spec, constraint)
    assert closest is not None  # parts_with_extents guarantees a non-empty sum exists
    best_total, evidence = closest
    if abs(best_total - constraint.value) <= constraint.tolerance + EPSILON:
        return None

    stated = ValueEvidence(
        location=f"constraint '{constraint.type.value}'",
        role=constraint.type.value,
        value=constraint.value,
        source=constraint.source,
        detail=f"tolerance ±{constraint.tolerance:g} mm",
    )
    achieved = ValueEvidence(
        location="parts",
        role=constraint.type.value,
        value=best_total,
        detail="closest achievable sum of part extents",
    )
    return ValidationIssue(
        rule=RuleId.CONSTRAINT_CLOSURE,
        message=(
            f"constraint '{constraint.type.value}' is {constraint.value:g} mm ±"
            f"{constraint.tolerance:g} mm, but the parts cannot sum to it — the closest "
            f"achievable sum is {best_total:g} mm"
        ),
        evidence=[stated, achieved, *evidence],
    )


def check_closure(spec: FurnitureSpec) -> list[ValidationIssue]:
    """Every overall constraint must be reachable by summing part extents."""
    issues: list[ValidationIssue] = []
    for _, group in groupby(
        (c for c in spec.constraints if c.type in _OVERALL_TYPES),
        key=lambda c: c.type,
    ):
        for constraint in group:
            issue = _closure_issue(constraint, spec)
            if issue is not None:
                issues.append(issue)
    return issues
