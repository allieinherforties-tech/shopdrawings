"""Constraint validation and traceability — the rule engine between extraction
and human review (blueprint §3, §5).

Public surface:

- ``validate_spec(spec)`` — the four structural checks; returns a
  ``ValidationReport`` in which every issue shows the values involved and the
  source crops they were read from.
- ``approval_blockers(spec)`` — those checks plus the schema's own review
  gate, the single call the pipeline makes before allowing approval.
"""

from .engine import approval_blockers, validate_spec
from .report import RuleId, ValidationIssue, ValidationReport, ValueEvidence

__all__ = [
    "RuleId",
    "ValidationIssue",
    "ValidationReport",
    "ValueEvidence",
    "approval_blockers",
    "validate_spec",
]
