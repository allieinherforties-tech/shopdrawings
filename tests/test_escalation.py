"""Escalation packages: everything a modeler needs, nothing fabricated."""

import json
from pathlib import Path
from typing import Any

import pytest
from ezdxf.entities.lwpolyline import LWPolyline
from ezdxf.filemanagement import readfile

from shop_drawings import FurnitureSpec
from shop_drawings.generation.errors import UnresolvablePartError
from shop_drawings.generation.escalation import (
    ESCALATION_DIR,
    Escalation,
    write_escalation_package,
)
from shop_drawings.generation.geometry import resolve_part


@pytest.fixture
def escalated(
    approved_spec_dict: dict[str, Any], approved_spec: FurnitureSpec, tmp_path: Path
) -> tuple[Escalation, FurnitureSpec, Path]:
    """The fixture panel mutated to three extents — outside the panel primitive."""
    approved_spec_dict["parts"][0]["dimensions"]["height"] = approved_spec_dict["parts"][0][
        "dimensions"
    ]["width"]
    spec = FurnitureSpec.model_validate(approved_spec_dict)
    part = spec.parts[0]
    with pytest.raises(UnresolvablePartError) as excinfo:
        resolve_part(part)
    return (
        write_escalation_package(spec, part, excinfo.value.reasons, tmp_path),
        spec,
        tmp_path,
    )


def test_the_package_records_reasons_questions_and_the_full_spec(
    escalated: tuple[Escalation, FurnitureSpec, Path], approved_spec_dict: dict[str, Any]
) -> None:
    record, spec, _ = escalated

    package = json.loads((record.directory / "package.json").read_text(encoding="utf-8"))
    assert record.part_id == "side_left"
    assert record.reasons and all("extent" in reason for reason in record.reasons)
    assert package["part_id"] == "side_left"
    assert package["reasons"] == list(record.reasons)
    assert package["open_questions"] == approved_spec_dict["unresolved_questions"]
    assert package["part"]["id"] == "side_left"
    assert package["spec"] == spec.model_dump(mode="json", by_alias=True)


def test_the_package_lives_under_the_escalation_directory(
    escalated: tuple[Escalation, FurnitureSpec, Path],
) -> None:
    record, _, _ = escalated

    assert record.files == (record.directory / "package.json", record.directory / "views.dxf")
    assert ESCALATION_DIR in record.directory.parts


def test_the_views_draw_three_declared_projections_with_labels(
    escalated: tuple[Escalation, FurnitureSpec, Path],
) -> None:
    record, _, _ = escalated

    doc = readfile(str(record.directory / "views.dxf"))
    view_rectangles = [
        entity
        for entity in doc.modelspace()
        if isinstance(entity, LWPolyline) and entity.dxf.layer == "VIEW"
    ]
    labels = [
        entity.dxf.text
        for entity in doc.modelspace()
        if entity.dxftype() == "TEXT" and entity.dxf.layer == "TEXT"
    ]

    assert len(view_rectangles) == 3
    assert all(rect.closed for rect in view_rectangles)
    assert doc.header.get("$INSUNITS") == 4  # follows the spec's declared units
    assert len(labels) == 3
    # The part declares three extents (720/380/380 after the escalation
    # mutation): plan and front collapse to the same rectangle, side is 380².
    label_bodies = [label.split(": ", 1)[1] for label in labels]
    assert label_bodies == ["720 x 380", "720 x 380", "380 x 380"]
