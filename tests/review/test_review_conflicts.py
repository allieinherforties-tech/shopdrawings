"""Conflict detection tests."""

from __future__ import annotations

import json
from pathlib import Path

from shop_drawings.review.conflicts import detect_conflicts, text_value_mismatch
from shop_drawings.review.models import ValueAddress
from shop_drawings.schema import (
    Dimension,
    DimensionKind,
    FurnitureSpec,
    ReviewStatus,
    SourceReference,
)


def _dim(value: float, text: str) -> Dimension:
    return Dimension(
        value=value,
        source=SourceReference(page=1, region=(0.1, 0.1, 0.4, 0.2), text=text),
        confidence=0.9,
    )


def test_detects_misread_value(spec_file: Path) -> None:
    spec = FurnitureSpec.model_validate_json(spec_file.read_text())
    conflicts = detect_conflicts(spec)
    kinds = [(c.kind, c.addresses[0].part_id) for c in conflicts]
    assert ("text_value_mismatch", "door_left") in kinds


def test_clean_source_produces_no_conflict(spec_file: Path) -> None:
    spec = FurnitureSpec.model_validate_json(spec_file.read_text())
    conflicts = detect_conflicts(spec)
    assert all(c.addresses[0].part_id != "bottom_panel" for c in conflicts)


def test_no_conflict_in_canonical_fixture() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "entry_cabinet.json"
    spec = FurnitureSpec.model_validate_json(fixture.read_text())
    assert detect_conflicts(spec) == []


def test_mismatch_tolerance_is_respected() -> None:
    dim = _dim(720.5, "720")
    dim = dim.model_copy(update={"tolerance": 0.5})
    address = ValueAddress(part_id="side_left", kind=DimensionKind.LENGTH)
    assert text_value_mismatch(address, dim) is None


def test_material_thickness_mismatch() -> None:
    spec_data = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "entry_cabinet.json").read_text()
    )
    spec_data["parts"][0]["material"] = "19mm birch plywood"
    spec = FurnitureSpec.model_validate(spec_data)
    conflicts = detect_conflicts(spec)
    assert any(c.kind == "material_thickness_mismatch" for c in conflicts)


def test_approved_conflict_leaves_the_queue(spec_file: Path) -> None:
    """Once the reviewer approves the disputed value, the conflict is ruled on."""
    spec = FurnitureSpec.model_validate_json(spec_file.read_text())
    from shop_drawings.review.conflicts import detect_conflicts
    from shop_drawings.review.models import build_queue

    conflicts = detect_conflicts(spec)
    assert conflicts  # the 396-vs-796 misread
    queue = build_queue(spec, conflicts)
    assert len(queue.conflicts) == 1

    door = next(p for p in spec.parts if p.id == "door_left")
    door.dimensions[DimensionKind.WIDTH].status = ReviewStatus.APPROVED
    queue = build_queue(spec, conflicts)
    assert queue.conflicts == []


def test_conflict_review_status_untouched() -> None:
    """Detection is read-only: it must never mutate review state."""
    dim = _dim(500, "700")
    address = ValueAddress(part_id="top", kind=DimensionKind.WIDTH)
    mismatch = text_value_mismatch(address, dim)
    assert mismatch is not None
    assert dim.status is ReviewStatus.NEEDS_REVIEW
