"""Part resolution: exact geometry from approved parts, escalation otherwise."""

import itertools
from typing import Any

import pytest

from shop_drawings import FurnitureSpec, PartType
from shop_drawings.generation.errors import UnresolvablePartError
from shop_drawings.generation.geometry import resolve_part
from tests.generation._builders import approved_feature, approved_part, sourced_dimension


def test_the_fixture_panel_resolves_to_exact_geometry(approved_spec: FurnitureSpec) -> None:
    plan = resolve_part(approved_spec.parts[0])

    assert plan.part_id == "side_left"
    assert plan.part_type is PartType.PANEL
    assert (plan.length, plan.width, plan.thickness) == (720.0, 380.0, 18.0)
    assert [edge.value for edge in plan.edge_banding] == ["front"]
    assert plan.pockets == ()


def test_fixture_shelf_pin_run_places_32mm_holes_37mm_from_the_front(
    approved_spec: FurnitureSpec,
) -> None:
    plan = resolve_part(approved_spec.parts[0])

    assert len(plan.drills) == 21
    assert all(drill.diameter == 5.0 for drill in plan.drills)
    assert all(drill.center_y == 37.0 for drill in plan.drills)
    xs = [drill.center_x for drill in plan.drills]
    assert xs[0] == 32.0  # one on-center spacing from the run origin
    assert xs[-1] == 672.0  # 48mm from the far end — at least one spacing
    assert xs == sorted(xs)
    assert all(after - before == 32.0 for before, after in itertools.pairwise(xs))


def test_a_panel_without_thickness_escalates(approved_spec_dict: dict[str, Any]) -> None:
    approved_spec_dict["parts"][0]["dimensions"].pop("thickness")
    spec = FurnitureSpec.model_validate(approved_spec_dict)

    with pytest.raises(UnresolvablePartError, match="no thickness dimension"):
        resolve_part(spec.parts[0])


@pytest.mark.parametrize("mutate", ["keep_length_only", "add_height"])
def test_a_panel_with_the_wrong_number_of_extents_escalates(
    approved_spec_dict: dict[str, Any], mutate: str
) -> None:
    dimensions = approved_spec_dict["parts"][0]["dimensions"]
    if mutate == "keep_length_only":
        approved_spec_dict["parts"][0]["dimensions"] = {"length": dimensions["length"]}
    else:
        dimensions["height"] = dimensions["width"]
    spec = FurnitureSpec.model_validate(approved_spec_dict)

    with pytest.raises(UnresolvablePartError, match="needs exactly"):
        resolve_part(spec.parts[0])


def test_a_tenon_escalates_instead_of_receiving_guessed_geometry(
    approved_spec_dict: dict[str, Any],
) -> None:
    tenon = approved_feature(
        "tenon",
        {"length": sourced_dimension(40)},
    )
    part = approved_part(
        approved_spec_dict,
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
        },
    )

    with pytest.raises(UnresolvablePartError, match="outside the v1 primitive library"):
        resolve_part(part)


def test_an_unknown_reference_edge_escalates_rather_than_guessing(
    approved_spec_dict: dict[str, Any],
) -> None:
    approved_spec_dict["parts"][0]["features"][0]["dimensions"]["setback"]["reference"] = (
        "mystery_edge"
    )
    spec = FurnitureSpec.model_validate(approved_spec_dict)

    with pytest.raises(UnresolvablePartError, match="not in the v1 vocabulary"):
        resolve_part(spec.parts[0])


def test_a_solid_uses_its_third_extent_as_thickness(approved_spec_dict: dict[str, Any]) -> None:
    part = approved_part(
        approved_spec_dict,
        {
            "id": "leg",
            "type": "solid",
            "quantity": 4,
            "material": "oak",
            "dimensions": {
                "length": sourced_dimension(720),
                "width": sourced_dimension(45),
                "height": sourced_dimension(45),
            },
            "features": [],
        },
    )

    plan = resolve_part(part)

    assert (plan.length, plan.width, plan.thickness) == (720.0, 45.0, 45.0)


def test_a_solid_contradicting_itself_escalates(approved_spec_dict: dict[str, Any]) -> None:
    part = approved_part(
        approved_spec_dict,
        {
            "id": "leg",
            "type": "solid",
            "quantity": 4,
            "material": "oak",
            "dimensions": {
                "length": sourced_dimension(720),
                "width": sourced_dimension(45),
                "height": sourced_dimension(45),
                "thickness": sourced_dimension(19),
            },
            "features": [],
        },
    )

    with pytest.raises(UnresolvablePartError, match="contradictory dimensions"):
        resolve_part(part)


def test_hardware_resolves_to_a_cut_list_only_row(approved_spec_dict: dict[str, Any]) -> None:
    part = approved_part(
        approved_spec_dict,
        {
            "id": "hinge",
            "type": "hardware",
            "quantity": 2,
            "material": "brass butt hinge",
            "dimensions": {},
            "features": [],
        },
    )

    plan = resolve_part(part)

    assert plan.part_type is PartType.HARDWARE
    assert (plan.length, plan.width, plan.thickness) == (0.0, 0.0, 0.0)
    assert plan.drills == () and plan.pockets == ()


def test_hardware_with_features_escalates(approved_spec_dict: dict[str, Any]) -> None:
    part = approved_part(
        approved_spec_dict,
        {
            "id": "hinge",
            "type": "hardware",
            "quantity": 2,
            "material": "brass butt hinge",
            "dimensions": {},
            "features": [
                approved_feature(
                    "hole",
                    {
                        "hole_diameter": sourced_dimension(4),
                        "setback": sourced_dimension(10, reference="front_edge"),
                        "edge_distance": sourced_dimension(20, reference="left"),
                    },
                )
            ],
        },
    )

    with pytest.raises(UnresolvablePartError, match="silently dropped"):
        resolve_part(part)
