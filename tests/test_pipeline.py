"""The pipeline entry point: gate, generate, verify — nothing unsafe ships."""

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from _builders import approved_feature, objects_of, read_model, sourced_dimension, spec_with_parts
from shop_drawings import FurnitureSpec
from shop_drawings.generation import generate
from shop_drawings.generation.config import GenerationConfig
from shop_drawings.generation.errors import FabricationError, NotApprovedError
from shop_drawings.generation.verify import CheckStatus


def test_the_approved_fixture_generates_model_sheets_and_cut_list(
    approved_spec: FurnitureSpec, default_config: GenerationConfig, tmp_path: Path
) -> None:
    result = generate(approved_spec, default_config, tmp_path)

    assert result.model_path is not None and result.model_path.exists()
    assert len(result.sheet_paths) == 1 and result.sheet_paths[0].exists()
    assert result.cut_list_path is not None and result.cut_list_path.exists()
    assert result.escalations == ()
    statuses = {check.name: check.status for check in result.checks}
    assert statuses["traceability"] is CheckStatus.PASS
    assert statuses["geometric_sanity"] is CheckStatus.PASS
    assert statuses["dxf_fabrication_checks"] is CheckStatus.PASS
    assert statuses["roundtrip_reproducibility"] is CheckStatus.PASS
    assert statuses["constraint_closure"] is CheckStatus.SKIPPED


def test_an_unapproved_spec_is_refused_before_anything_is_written(
    approved_spec_dict: dict[str, Any], default_config: GenerationConfig, tmp_path: Path
) -> None:
    approved_spec_dict["status"] = "needs_review"

    with pytest.raises(NotApprovedError):
        generate(FurnitureSpec.model_validate(approved_spec_dict), default_config, tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_an_unresolvable_part_escalates_while_the_rest_generates(
    approved_spec_dict: dict[str, Any], default_config: GenerationConfig, tmp_path: Path
) -> None:
    tenon = approved_feature("tenon", {"length": sourced_dimension(40)})
    spec = spec_with_parts(
        approved_spec_dict,
        [
            {
                "id": "shelf_tenon",
                "type": "solid",
                "quantity": 4,
                "material": "oak",
                "dimensions": {
                    "length": sourced_dimension(300),
                    "width": sourced_dimension(20),
                    "height": sourced_dimension(20),
                },
                "features": [tenon],
            }
        ],
    )

    result = generate(spec, default_config, tmp_path)

    assert len(result.escalations) == 1
    escalation = result.escalations[0]
    assert escalation.part_id == "shelf_tenon"
    assert all(path.exists() for path in escalation.files)
    package = json.loads(escalation.files[0].read_text(encoding="utf-8"))
    assert package["reasons"]
    # The resolvable panel still generates its model and sheets.
    assert result.model_path is not None
    model = read_model(result.model_path)
    part_names = [
        obj.Attributes.Name
        for obj in objects_of(model)
        if obj.Attributes.Name and not obj.Attributes.Name.startswith("side_left_")
    ]
    assert part_names == ["side_left"]
    # The cut list covers every part, escalated or not.
    cut_list_path = result.cut_list_path
    assert cut_list_path is not None and cut_list_path.exists()
    with cut_list_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert {row[0] for row in rows[1:]} == {"side_left", "shelf_tenon"}


def test_a_failed_verification_removes_every_written_file(
    approved_spec_dict: dict[str, Any], default_config: GenerationConfig, tmp_path: Path
) -> None:
    feature = approved_spec_dict["parts"][0]["features"][0]
    feature["dimensions"]["setback"]["value"] = 379  # holes reach past the panel edge
    spec = FurnitureSpec.model_validate(approved_spec_dict)

    with pytest.raises(FabricationError, match="failed verification"):
        generate(spec, default_config, tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_a_hardware_only_spec_writes_just_the_cut_list(
    approved_spec_dict: dict[str, Any], default_config: GenerationConfig, tmp_path: Path
) -> None:
    approved_spec_dict["parts"] = [
        {
            "id": "hinge",
            "type": "hardware",
            "quantity": 2,
            "material": "brass butt hinge",
            "dimensions": {},
            "features": [],
            "status": "approved",
        }
    ]
    spec = FurnitureSpec.model_validate(approved_spec_dict)

    result = generate(spec, default_config, tmp_path)

    assert result.model_path is None
    assert result.sheet_paths == ()
    cut_list_path = result.cut_list_path
    assert cut_list_path is not None and cut_list_path.exists()
    statuses = {check.name: check.status for check in result.checks}
    assert statuses["traceability"] is CheckStatus.PASS
    assert statuses["geometric_sanity"] is CheckStatus.PASS
    assert "dxf_fabrication_checks" not in statuses
    assert "roundtrip_reproducibility" not in statuses
    with cut_list_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[1] == ["hinge", "hardware", "2", "brass butt hinge", "", "", "", "", "", ""]
