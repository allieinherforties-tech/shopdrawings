"""The generator's side of the review gate (blueprint §4).

The schema refuses to *construct* an approved-but-blocked spec; this
module refuses to *generate* from anything that has not been approved.
Both halves exist so the gate holds even if a spec bypasses validation
via ``model_construct`` or is loaded from an untrusted file.
"""

from __future__ import annotations

from shop_drawings.generation.errors import NotApprovedError
from shop_drawings.schema import FurnitureSpec, ReviewStatus


def ensure_generatable(spec: FurnitureSpec) -> None:
    """Raise unless the spec is approved with an empty blocking list."""
    if spec.status is not ReviewStatus.APPROVED:
        raise NotApprovedError(
            f"spec '{spec.project.name}' is {spec.status.value}, not approved — "
            "the review gate blocks generation"
        )
    errors = spec.approval_errors()
    if errors:
        raise NotApprovedError(
            f"spec '{spec.project.name}' fails the review gate: " + "; ".join(errors)
        )
