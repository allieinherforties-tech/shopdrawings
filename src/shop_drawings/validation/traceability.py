"""Traceability enforcement — blueprint §5: "a spec with an untraceable value
is invalid, full stop."

The schema already makes an untraceable *value* unconstructible (every
``TracedValue`` requires a ``SourceReference``). This module is the
independent second check the pipeline relies on before review: it walks every
dimension and constraint in a constructed spec, re-verifies each source, and
flags parts that carry no evidence at all — so a spec can never reach the
review queue with a value nobody can point back to a page for.
"""

from __future__ import annotations

from shop_drawings.schema import (
    Constraint,
    FurnitureSpec,
    Part,
    SourceReference,
    TracedValue,
)

from .report import RuleId, ValidationIssue, ValueEvidence


def _location(kind: str, part: Part, feature_index: int | None) -> str:
    """Stable, human-readable name for where a value lives in the spec."""
    base = f"part '{part.id}'"
    if feature_index is not None:
        feature = part.features[feature_index]
        base += f" feature[{feature_index}] ({feature.type.value})"
    return f"{base} dimension '{kind}'"


def _source_is_well_formed(source: SourceReference) -> bool:
    """Re-verify the source contract independently of the schema validators."""
    return source.page >= 1 and bool(source.text.strip()) and bool(source.region)


def _check_value(
    value: TracedValue,
    *,
    location: str,
    role: str,
    issues: list[ValidationIssue],
) -> None:
    """Flag any value whose source is malformed or degenerate.

    A *missing* source cannot occur through normal construction — the schema
    requires one and tests pin that contract — so what this check catches in
    practice is a source that exists but points nowhere: page 0, empty text,
    or a zero-area region.
    """
    source = value.source
    if not _source_is_well_formed(source):
        issues.append(
            ValidationIssue(
                rule=RuleId.TRACEABILITY,
                message=(
                    f"{location} carries a malformed source reference "
                    f"(page {source.page}, text {source.text!r})"
                ),
                evidence=[
                    ValueEvidence(location=location, role=role, value=value.value, source=source)
                ],
            )
        )


def _check_part(part: Part, issues: list[ValidationIssue]) -> None:
    for kind, dimension in part.dimensions.items():
        _check_value(
            dimension,
            location=_location(kind.value, part, None),
            role=kind.value,
            issues=issues,
        )
    for feature_index, feature in enumerate(part.features):
        for kind, dimension in feature.dimensions.items():
            _check_value(
                dimension,
                location=_location(kind.value, part, feature_index),
                role=kind.value,
                issues=issues,
            )
    if not part.dimensions and not part.features and not part.source_references:
        issues.append(
            ValidationIssue(
                rule=RuleId.TRACEABILITY,
                message=(
                    f"part '{part.id}' carries no dimensions, features, or source "
                    "references — nothing ties it to the design"
                ),
                evidence=[
                    ValueEvidence(location=f"part '{part.id}'", role="existence", value=None)
                ],
            )
        )


def _check_constraint(constraint: Constraint, issues: list[ValidationIssue]) -> None:
    _check_value(
        constraint,
        location=f"constraint '{constraint.type.value}'",
        role=constraint.type.value,
        issues=issues,
    )


def check_traceability(spec: FurnitureSpec) -> list[ValidationIssue]:
    """Every value in the spec must trace to a page, region, and text."""
    issues: list[ValidationIssue] = []
    for part in spec.parts:
        _check_part(part, issues)
    for constraint in spec.constraints:
        _check_constraint(constraint, issues)
    return issues
