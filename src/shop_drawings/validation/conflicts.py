"""Conflict grouping — blueprint §4: when two extracted values claim the same
role, both are surfaced together, never silently picked from.

Two claims conflict when their values cannot both be true within their own
tolerances. Each issue carries *every* value in the group with its source
crop, so the reviewer compares them side by side and resolves one queue
entry rather than hunting duplicate facts across the spec.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from itertools import combinations
from typing import TypeVar

from shop_drawings.schema import Constraint, FurnitureSpec, Part, TracedValue

from .report import RuleId, ValidationIssue, ValueEvidence

_EPSILON = 1e-9

V = TypeVar("V", bound=TracedValue)


def _values_conflict(first: TracedValue, second: TracedValue) -> bool:
    """Two values conflict when their claims cannot both hold within tolerance."""
    gap = abs(first.value - second.value)
    return gap > first.tolerance + second.tolerance + _EPSILON


def _evidence_for(location: str, role: str, value: TracedValue) -> ValueEvidence:
    return ValueEvidence(
        location=location,
        role=role,
        value=value.value,
        source=value.source,
        detail=f"tolerance ±{value.tolerance:g} mm",
    )


def _grouped_issues(
    items: list[tuple[str, str, V]],
    *,
    rule_message: Callable[[str, list[tuple[str, str, V]]], str],
) -> list[ValidationIssue]:
    """Group values by role; emit one issue per role whose members disagree.

    ``items`` are (location, role, value) triples; roles group claims that
    must agree, locations name where each claim was extracted from.
    """
    by_role: dict[str, list[tuple[str, str, V]]] = defaultdict(list)
    for item in items:
        by_role[item[1]].append(item)

    issues: list[ValidationIssue] = []
    for role, members in sorted(by_role.items()):
        disagreeing = any(
            _values_conflict(first[2], second[2]) for first, second in combinations(members, 2)
        )
        if not disagreeing:
            continue
        evidence = [_evidence_for(location, role, value) for location, _, value in members]
        issues.append(
            ValidationIssue(
                rule=RuleId.CONFLICT,
                message=rule_message(role, members),
                evidence=evidence,
            )
        )
    return issues


def check_constraint_conflicts(spec: FurnitureSpec) -> list[ValidationIssue]:
    """Two constraints of the same type with irreconcilable values block review."""

    def message(role: str, members: list[tuple[str, str, Constraint]]) -> str:
        values = " and ".join(f"{value.value:g} mm" for _, _, value in members)
        return (
            f"{len(members)} extracted values claim the role '{role}': {values} — "
            "they cannot both be right; resolve which the design means"
        )

    items = [
        (f"constraint '{constraint.type.value}'", constraint.type.value, constraint)
        for constraint in spec.constraints
    ]
    return _grouped_issues(items, rule_message=message)


def check_duplicate_part_ids(spec: FurnitureSpec) -> list[ValidationIssue]:
    """A part id names one part in the generated files; two parts claiming it
    is a conflict the generator could silently collapse."""

    by_id: dict[str, list[Part]] = defaultdict(list)
    for part in spec.parts:
        by_id[part.id].append(part)

    issues: list[ValidationIssue] = []
    for part_id, duplicates in sorted(by_id.items()):
        if len(duplicates) < 2:
            continue
        issues.append(
            ValidationIssue(
                rule=RuleId.CONFLICT,
                message=(
                    f"part id '{part_id}' is claimed by {len(duplicates)} parts — ids become "
                    "object names in generated files, so one id must mean one part"
                ),
                evidence=[
                    ValueEvidence(
                        location=f"part '{part_id}'",
                        role="part_id",
                        value=None,
                        detail=f"claimed by {len(duplicates)} parts",
                    )
                ],
            )
        )
    return issues


def check_conflicts(spec: FurnitureSpec) -> list[ValidationIssue]:
    """Every role claimed more than once must agree, or block with all values shown."""
    return [
        *check_constraint_conflicts(spec),
        *check_duplicate_part_ids(spec),
    ]
