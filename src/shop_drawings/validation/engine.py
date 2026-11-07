"""The validation engine: one call between extraction and the review gate.

``validate_spec`` runs the four structural checks from blueprint §5 —
traceability, conflict grouping, constraint closure, geometric sanity — and
returns every issue with both values and their source crops attached.

``approval_blockers`` composes that report with the schema's own review gate
(``FurnitureSpec.approval_errors``), quoted rather than reimplemented, so a
caller needs exactly one function to decide whether a spec may be approved:

    blockers = approval_blockers(spec)
    if not blockers:
        spec.status = ReviewStatus.APPROVED  # safe: gate + validation pass

Nothing here mutates the spec or touches the world; the layer is pure so it
can run in tests, in CI, and in the review UI against the same objects.
"""

from __future__ import annotations

from shop_drawings.schema import FurnitureSpec

from .closure import check_closure
from .conflicts import check_conflicts
from .geometry import check_geometry
from .report import RuleId, ValidationIssue, ValidationReport
from .traceability import check_traceability


def validate_spec(spec: FurnitureSpec) -> ValidationReport:
    """Run every structural check on a spec and report all issues at once."""
    issues = [
        *check_traceability(spec),
        *check_conflicts(spec),
        *check_closure(spec),
        *check_geometry(spec),
    ]
    return ValidationReport(issues=issues)


def approval_blockers(spec: FurnitureSpec) -> list[ValidationIssue]:
    """Everything blocking this spec from being approved — gate and structure.

    The review-gate entries come from the schema's ``approval_errors()``, the
    same list the model validator enforces on construction; they are surfaced
    here so the reviewer sees gate and structure failures in one queue.
    """
    gate_issues = [
        ValidationIssue(rule=RuleId.REVIEW_GATE, message=error) for error in spec.approval_errors()
    ]
    return [*gate_issues, *validate_spec(spec).issues]
