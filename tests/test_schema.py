"""Structural validation: the blueprint example parses; malformed specs fail loudly."""

from typing import Any

import pytest
from pydantic import ValidationError

from shop_drawings import (
    ConstraintType,
    DimensionKind,
    Edge,
    FeatureType,
    FurnitureSpec,
    PartType,
    ReviewStatus,
    Units,
)


def test_parses_the_blueprint_cabinet_example(cabinet_spec_dict: dict[str, Any]) -> None:
    spec = FurnitureSpec.model_validate(cabinet_spec_dict)

    assert spec.schema_version == 1
    assert spec.project.name == "entry-cabinet-001"
    assert spec.project.units is Units.MM
    assert spec.project.source == "redline-v3.pdf"

    part = spec.parts[0]
    assert part.id == "side_left"
    assert part.type is PartType.PANEL
    assert part.material == "18mm birch plywood"
    assert part.edge_banding == [Edge.FRONT]
    assert part.dimensions[DimensionKind.LENGTH].value == 720
    assert part.dimensions[DimensionKind.WIDTH].value == 380
    assert part.dimensions[DimensionKind.THICKNESS].value == 18

    feature = part.features[0]
    assert feature.type is FeatureType.SHELF_PIN_HOLES
    assert feature.dimensions[DimensionKind.HOLE_DIAMETER].value == 5
    assert feature.dimensions[DimensionKind.SPACING].value == 32
    setback = feature.dimensions[DimensionKind.SETBACK]
    assert setback.value == 37
    assert setback.reference == "front_edge"
    assert setback.confidence == pytest.approx(0.78)
    assert setback.source.page == 1
    assert setback.source.text == "37"

    constraint = spec.constraints[0]
    assert constraint.type is ConstraintType.OVERALL_WIDTH
    assert constraint.value == 800
    assert constraint.tolerance == 1

    assert len(spec.unresolved_questions) == 1
    assert spec.status is ReviewStatus.NEEDS_REVIEW


def test_round_trips_through_json(cabinet_spec_dict: dict[str, Any]) -> None:
    spec = FurnitureSpec.model_validate(cabinet_spec_dict)

    encoded = spec.model_dump_json()
    reloaded = FurnitureSpec.model_validate_json(encoded)

    assert reloaded == spec
    assert reloaded.model_dump_json() == encoded


def test_rejects_a_dimension_without_a_source_reference(cabinet_spec_dict: dict[str, Any]) -> None:
    del cabinet_spec_dict["parts"][0]["dimensions"]["length"]["source"]

    with pytest.raises(ValidationError, match="source"):
        FurnitureSpec.model_validate(cabinet_spec_dict)


def test_rejects_an_unknown_dimension_kind(cabinet_spec_dict: dict[str, Any]) -> None:
    length = cabinet_spec_dict["parts"][0]["dimensions"]["length"]
    cabinet_spec_dict["parts"][0]["dimensions"]["diagonal"] = length

    with pytest.raises(ValidationError, match="diagonal"):
        FurnitureSpec.model_validate(cabinet_spec_dict)


def test_rejects_an_unknown_feature_type(cabinet_spec_dict: dict[str, Any]) -> None:
    cabinet_spec_dict["parts"][0]["features"][0]["type"] = "french_polish"

    with pytest.raises(ValidationError, match="french_polish"):
        FurnitureSpec.model_validate(cabinet_spec_dict)


def test_rejects_an_unknown_constraint_type(cabinet_spec_dict: dict[str, Any]) -> None:
    cabinet_spec_dict["constraints"][0]["type"] = "roughly_square"

    with pytest.raises(ValidationError, match="roughly_square"):
        FurnitureSpec.model_validate(cabinet_spec_dict)


def test_rejects_an_unknown_part_type(cabinet_spec_dict: dict[str, Any]) -> None:
    cabinet_spec_dict["parts"][0]["type"] = "wedge"

    with pytest.raises(ValidationError, match="wedge"):
        FurnitureSpec.model_validate(cabinet_spec_dict)


def test_rejects_an_unknown_edge_name(cabinet_spec_dict: dict[str, Any]) -> None:
    cabinet_spec_dict["parts"][0]["edge_banding"] = ["frontish"]

    with pytest.raises(ValidationError, match="frontish"):
        FurnitureSpec.model_validate(cabinet_spec_dict)


def test_rejects_a_region_outside_the_page(cabinet_spec_dict: dict[str, Any]) -> None:
    source = cabinet_spec_dict["parts"][0]["dimensions"]["length"]["source"]
    source["region"] = [0.12, 0.32, 1.41, 0.78]

    with pytest.raises(ValidationError, match="normalized crop"):
        FurnitureSpec.model_validate(cabinet_spec_dict)


def test_rejects_a_region_with_no_area(cabinet_spec_dict: dict[str, Any]) -> None:
    source = cabinet_spec_dict["parts"][0]["dimensions"]["length"]["source"]
    source["region"] = [0.5, 0.5, 0.5, 0.9]

    with pytest.raises(ValidationError, match="normalized crop"):
        FurnitureSpec.model_validate(cabinet_spec_dict)


def test_rejects_confidence_above_one(cabinet_spec_dict: dict[str, Any]) -> None:
    cabinet_spec_dict["parts"][0]["dimensions"]["length"]["confidence"] = 1.5

    with pytest.raises(ValidationError, match="confidence"):
        FurnitureSpec.model_validate(cabinet_spec_dict)


def test_rejects_misspelled_fields(cabinet_spec_dict: dict[str, Any]) -> None:
    cabinet_spec_dict["constrants"] = cabinet_spec_dict.pop("constraints")

    with pytest.raises(ValidationError, match="constrants"):
        FurnitureSpec.model_validate(cabinet_spec_dict)


def test_rejects_an_unknown_schema_version(cabinet_spec_dict: dict[str, Any]) -> None:
    cabinet_spec_dict["schema_version"] = 2

    with pytest.raises(ValidationError, match="schema_version"):
        FurnitureSpec.model_validate(cabinet_spec_dict)
