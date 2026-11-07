"""Review-gate domain models: value addressing, the review queue, and the log.

The queue implements blueprint §4's priority order — blocked questions,
conflicts, low-confidence associations, then everything else — as data the
UI renders in that order.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from shop_drawings.schema import (
    Dimension,
    DimensionKind,
    FeatureType,
    FurnitureSpec,
    ReviewStatus,
    SourceReference,
)

#: Values at or below this confidence are routed to the low-confidence
#: queue rather than the batch-confirm section (blueprint §4).
LOW_CONFIDENCE_THRESHOLD = 0.85


class ValueAddress(BaseModel):
    """Where a single dimension lives in a spec.

    ``feature_index`` is ``None`` for part-level dimensions and the index
    into ``part.features`` otherwise. Unknown fields are typos, so reject.
    """

    model_config = ConfigDict(extra="forbid")

    part_id: str
    feature_index: int | None = None
    kind: DimensionKind


class Conflict(BaseModel):
    """Two extracted facts that disagree, shown side by side with their crops."""

    kind: str
    description: str
    addresses: list[ValueAddress]
    sources: list[SourceReference] = Field(default_factory=list)


class QueueValue(BaseModel):
    """One dimension as the review queue presents it."""

    address: ValueAddress
    label: str
    kind: DimensionKind
    value: float
    tolerance: float
    confidence: float
    status: str
    source: SourceReference


class QueuePart(BaseModel):
    """A part whose own review status is still needs_review."""

    part_id: str
    status: str


class QueueConstraint(BaseModel):
    """An assembly constraint awaiting review."""

    index: int
    type: str
    value: float
    confidence: float
    status: str
    source: SourceReference


class ReviewQueue(BaseModel):
    """The reviewer's worklist in blueprint §4 priority order."""

    questions: list[str]
    conflicts: list[Conflict]
    low_confidence: list[QueueValue]
    unreviewed: list[QueueValue]
    parts: list[QueuePart]
    constraints: list[QueueConstraint]
    total_values: int
    approved_values: int


class LogEntry(BaseModel):
    """One recorded reviewer action. Corrections go into the spec; the log
    records that they happened, and what the question's answer was."""

    ts: str
    action: str
    detail: str
    address: ValueAddress | None = None
    before: float | None = None
    after: float | None = None


def iter_dimensions(
    spec: FurnitureSpec,
) -> Iterator[tuple[ValueAddress, str, Dimension]]:
    """Every dimension in the spec with its address and display label.

    Label shape: ``side_left · width`` or
    ``side_left · shelf_pin_holes · setback``.
    """
    for part in spec.parts:
        for kind, dim in part.dimensions.items():
            yield (
                ValueAddress(part_id=part.id, kind=kind),
                f"{part.id} · {kind.value}",
                dim,
            )
        for index, feature in enumerate(part.features):
            for kind, dim in feature.dimensions.items():
                yield (
                    ValueAddress(part_id=part.id, feature_index=index, kind=kind),
                    f"{part.id} · {feature.type.value} · {kind.value}",
                    dim,
                )


def feature_label(spec: FurnitureSpec, address: ValueAddress) -> str:
    """Rebuild the display label for an address (used by the log viewer)."""
    for part in spec.parts:
        if part.id != address.part_id:
            continue
        if address.feature_index is None:
            return f"{part.id} · {address.kind.value}"
        features = part.features
        if 0 <= address.feature_index < len(features):
            feature_type: FeatureType = features[address.feature_index].type
            return f"{part.id} · {feature_type.value} · {address.kind.value}"
    return f"{address.part_id} · {address.kind.value}"


def dimension_map(spec: FurnitureSpec, address: ValueAddress) -> dict[DimensionKind, Dimension]:
    """The dimension dict an address points into (part-level or feature-level).

    Raises ``KeyError`` for an unknown part, and ``IndexError`` for a
    feature index that does not exist — callers turn those into 404s.
    """
    for part in spec.parts:
        if part.id != address.part_id:
            continue
        if address.feature_index is None:
            return part.dimensions
        return part.features[address.feature_index].dimensions
    raise KeyError(address.part_id)


def build_queue(
    spec: FurnitureSpec,
    conflicts: list[Conflict] | None = None,
) -> ReviewQueue:
    """Slice the spec's values into the reviewer's priority-ordered worklist.

    Conflicts are computed by ``conflicts.detect_conflicts`` and passed in —
    kept as a parameter to avoid a circular import.
    """
    low_confidence: list[QueueValue] = []
    unreviewed: list[QueueValue] = []
    approved = 0
    total = 0
    for address, label, dim in iter_dimensions(spec):
        total += 1
        queue_value = QueueValue(
            address=address,
            label=label,
            kind=address.kind,
            value=dim.value,
            tolerance=dim.tolerance,
            confidence=dim.confidence,
            status=dim.status.value,
            source=dim.source,
        )
        if dim.status.value == "approved":
            approved += 1
        elif dim.confidence <= LOW_CONFIDENCE_THRESHOLD:
            low_confidence.append(queue_value)
        else:
            unreviewed.append(queue_value)

    low_confidence.sort(key=lambda v: v.confidence)
    # A conflict whose every addressed value is approved has been ruled on
    # by the reviewer — it no longer belongs in the active queue.
    approved_keys = {
        (address.part_id, address.feature_index, address.kind.value)
        for address, _, dim in iter_dimensions(spec)
        if dim.status is ReviewStatus.APPROVED
    }

    def active(conflict: Conflict) -> bool:
        return not all(
            (a.part_id, a.feature_index, a.kind.value) in approved_keys for a in conflict.addresses
        )

    return ReviewQueue(
        questions=list(spec.unresolved_questions),
        conflicts=[c for c in (conflicts if conflicts is not None else []) if active(c)],
        low_confidence=low_confidence,
        unreviewed=unreviewed,
        parts=[
            QueuePart(part_id=part.id, status=part.status.value)
            for part in spec.parts
            if part.status is not ReviewStatus.APPROVED
        ],
        constraints=[
            QueueConstraint(
                index=index,
                type=constraint.type.value,
                value=constraint.value,
                confidence=constraint.confidence,
                status=constraint.status.value,
                source=constraint.source,
            )
            for index, constraint in enumerate(spec.constraints)
            if constraint.status is not ReviewStatus.APPROVED
        ],
        total_values=total,
        approved_values=approved,
    )


def spec_payload(spec: FurnitureSpec) -> dict[str, Any]:
    """The spec as JSON-safe data for the API layer."""
    payload: dict[str, Any] = spec.model_dump(mode="json")
    return payload
