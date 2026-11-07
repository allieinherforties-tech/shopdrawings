"""Geometric sanity: thickness fits, holes within edges, possible reveals."""

from typing import Any

from shop_drawings import FurnitureSpec
from shop_drawings.validation import RuleId, validate_spec


def geometry_issues(spec_dict: dict[str, Any]) -> list[str]:
    report = validate_spec(FurnitureSpec.model_validate(spec_dict))
    return [issue.message for issue in report.issues if issue.rule is RuleId.GEOMETRIC_SANITY]


def test_a_sane_cabinet_has_no_geometry_issues(cabinet: dict[str, Any]) -> None:
    assert geometry_issues(cabinet) == []


def test_a_panel_thicker_than_its_face_blocks(make_spec: Any) -> None:
    spec = make_spec()
    spec["parts"][0]["dimensions"]["thickness"]["value"] = 500  # faces are 720 x 380

    issues = geometry_issues(spec)

    assert any("thicker" in issue for issue in issues)
    assert any("500" in issue for issue in issues)


def test_a_hole_larger_than_the_face_blocks(make_spec: Any) -> None:
    spec = make_spec()
    spec["parts"][0]["features"][0]["dimensions"]["hole_diameter"]["value"] = 400

    issues = geometry_issues(spec)

    assert any("cannot fit within" in issue for issue in issues)


def test_overlapping_holes_block(make_spec: Any) -> None:
    spec = make_spec()
    feature = spec["parts"][0]["features"][0]["dimensions"]
    feature["hole_diameter"]["value"] = 40
    feature["spacing"]["value"] = 32

    issues = geometry_issues(spec)

    assert any("touch or overlap" in issue for issue in issues)


def test_a_hole_breaking_out_of_the_edge_blocks(make_spec: Any) -> None:
    spec = make_spec()
    spec["parts"][0]["features"][0]["dimensions"]["setback"]["value"] = 719  # + 2.5 > 720

    issues = geometry_issues(spec)

    assert any("breaks out of the edge" in issue for issue in issues)


def test_a_hole_pattern_that_fits_passes(make_spec: Any) -> None:
    spec = make_spec()
    spec["parts"][0]["features"][0]["dimensions"]["setback"]["value"] = 37

    assert geometry_issues(spec) == []


def test_a_clear_larger_than_the_overall_blocks(make_spec: Any, constraint: Any) -> None:
    spec = make_spec(constraints=[constraint("overall_width", 800), constraint("clear_width", 900)])

    issues = geometry_issues(spec)

    assert any("inside cannot be larger than the outside" in issue for issue in issues)


def test_a_clear_with_no_stock_for_the_walls_blocks(make_spec: Any, constraint: Any) -> None:
    """700 clear + two 18 mm walls = 736 <= 800 passes; 770 clear cannot."""
    fitting = make_spec(
        constraints=[constraint("overall_width", 800), constraint("clear_width", 700)]
    )
    assert geometry_issues(fitting) == []

    impossible = make_spec(
        constraints=[constraint("overall_width", 800), constraint("clear_width", 770)]
    )
    issues = geometry_issues(impossible)

    assert any("leave no stock" in issue for issue in issues)


def test_a_reveal_larger_than_its_opening_blocks(make_spec: Any, constraint: Any) -> None:
    spec = make_spec(
        constraints=[constraint("clear_width", 40)],
    )
    spec["parts"][0]["dimensions"]["reveal"] = {
        "value": 50,
        "source": {"page": 1, "region": [0.2, 0.2, 0.3, 0.3], "text": "50"},
        "confidence": 0.9,
        "status": "approved",
    }

    issues = geometry_issues(spec)

    assert any("reveal" in issue for issue in issues)
    assert any("40" in issue for issue in issues)


def test_a_plausible_reveal_passes(make_spec: Any, constraint: Any) -> None:
    spec = make_spec(constraints=[constraint("clear_width", 700)])
    spec["parts"][0]["dimensions"]["reveal"] = {
        "value": 3,
        "source": {"page": 1, "region": [0.2, 0.2, 0.3, 0.3], "text": "3"},
        "confidence": 0.9,
        "status": "approved",
    }

    assert geometry_issues(spec) == []
