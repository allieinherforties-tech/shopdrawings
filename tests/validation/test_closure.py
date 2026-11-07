"""Constraint closure: summed part extents must reach each overall dimension."""

from typing import Any

from shop_drawings import FurnitureSpec
from shop_drawings.validation import RuleId, validate_spec


def closure_issues(spec_dict: dict[str, Any]) -> list[str]:
    report = validate_spec(FurnitureSpec.model_validate(spec_dict))
    return [issue.message for issue in report.issues if issue.rule is RuleId.CONSTRAINT_CLOSURE]


def test_a_closing_cabinet_has_no_closure_issues(cabinet: dict[str, Any]) -> None:
    assert closure_issues(cabinet) == []


def test_parts_that_cannot_sum_block_with_both_values(make_spec: Any, constraint: Any) -> None:
    """The blueprint §4 conflict: a 720 mm side panel against an 800 mm overall."""
    side = {
        "id": "side_left",
        "type": "panel",
        "quantity": 1,
        "material": "ply",
        "dimensions": {
            "length": {
                "value": 720,
                "source": {"page": 1, "region": [0.1, 0.1, 0.2, 0.2], "text": "720"},
                "confidence": 0.9,
                "status": "approved",
            }
        },
        "features": [],
        "edge_banding": [],
        "source_references": [],
        "status": "approved",
    }
    spec = make_spec(parts=[side], constraints=[constraint("overall_width", 800)])
    issues = closure_issues(spec)

    assert len(issues) == 1
    assert "800" in issues[0]
    assert "closest" in issues[0]
    assert "720" in issues[0]


def test_tolerance_absorbs_small_deviation(make_spec: Any, constraint: Any) -> None:
    spec = make_spec(constraints=[constraint("overall_width", 800, tolerance=1)])
    spec["parts"][1]["dimensions"]["width"]["value"] = 763.5  # sum: 799.5

    assert closure_issues(spec) == []


def test_quantities_multiply_into_the_sum(make_spec: Any, constraint: Any) -> None:
    """Four 18 mm panels close a 72 mm overall; two cannot."""
    panel = {
        "id": "side",
        "type": "panel",
        "quantity": 4,
        "material": "ply",
        "dimensions": {
            "thickness": {
                "value": 18,
                "source": {"page": 1, "region": [0.1, 0.1, 0.2, 0.2], "text": "18"},
                "confidence": 0.9,
                "status": "approved",
            }
        },
        "features": [],
        "edge_banding": [],
        "source_references": [],
        "status": "approved",
    }

    closing = make_spec(parts=[panel], constraints=[constraint("overall_width", 72, tolerance=0)])
    assert closure_issues(closing) == []

    short = make_spec(
        parts=[dict(panel, quantity=2)], constraints=[constraint("overall_width", 72, tolerance=0)]
    )
    assert closure_issues(short), "two 18 mm panels cannot make 72 mm"


def test_closure_without_part_extents_cannot_be_trusted(make_spec: Any, constraint: Any) -> None:
    spec = make_spec(parts=[], constraints=[constraint("overall_width", 800)])

    issues = closure_issues(spec)

    assert len(issues) == 1
    assert "closure cannot be checked" in issues[0]


def test_clear_constraints_do_not_participate_in_closure(make_spec: Any, constraint: Any) -> None:
    """Clear openings are checked geometrically, not by summing parts."""
    spec = make_spec(constraints=[constraint("clear_width", 700)])

    assert closure_issues(spec) == []
