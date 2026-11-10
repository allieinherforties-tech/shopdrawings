"""The review gate: approval is impossible while anything is unresolved."""

from typing import Any

import pytest
from pydantic import ValidationError

from shop_drawings import FurnitureSpec, ReviewStatus


def test_a_draft_spec_may_carry_unresolved_questions(cabinet_spec_dict: dict[str, Any]) -> None:
    spec = FurnitureSpec.model_validate(cabinet_spec_dict)

    assert spec.status is ReviewStatus.NEEDS_REVIEW
    assert spec.approval_errors(), "a draft with open questions is not approvable"


def test_a_fully_approved_spec_passes_the_gate(approved_spec_dict: dict[str, Any]) -> None:
    spec = FurnitureSpec.model_validate(approved_spec_dict)

    assert spec.status is ReviewStatus.APPROVED
    assert spec.approval_errors() == []

    encoded = spec.model_dump_json()
    reloaded = FurnitureSpec.model_validate_json(encoded)
    assert reloaded == spec
    assert reloaded.model_dump_json() == encoded


def test_approval_errors_name_every_blocker(cabinet_spec_dict: dict[str, Any]) -> None:
    spec = FurnitureSpec.model_validate(cabinet_spec_dict)

    errors = spec.approval_errors()

    assert any("unresolved question" in error for error in errors)
    assert any("side_left" in error for error in errors)
    assert any("'length'" in error for error in errors)
    assert any("shelf_pin_holes" in error for error in errors)
    assert any("overall_width" in error for error in errors)


def test_an_approved_spec_with_unresolved_questions_is_rejected(
    approved_spec_dict: dict[str, Any],
) -> None:
    approved_spec_dict["unresolved_questions"] = [
        "Confirm whether the 380mm depth includes the door front."
    ]

    with pytest.raises(ValidationError, match="review gate"):
        FurnitureSpec.model_validate(approved_spec_dict)


def test_an_approved_spec_with_an_unreviewed_part_is_rejected(
    approved_spec_dict: dict[str, Any],
) -> None:
    approved_spec_dict["parts"][0]["status"] = "needs_review"

    with pytest.raises(ValidationError, match="review gate"):
        FurnitureSpec.model_validate(approved_spec_dict)


def test_an_approved_spec_with_an_unreviewed_dimension_is_rejected(
    approved_spec_dict: dict[str, Any],
) -> None:
    approved_spec_dict["parts"][0]["dimensions"]["width"]["status"] = "needs_review"

    with pytest.raises(ValidationError, match="review gate"):
        FurnitureSpec.model_validate(approved_spec_dict)


def test_an_approved_spec_with_an_unreviewed_feature_dimension_is_rejected(
    approved_spec_dict: dict[str, Any],
) -> None:
    approved_spec_dict["parts"][0]["features"][0]["dimensions"]["spacing"]["status"] = (
        "needs_review"
    )

    with pytest.raises(ValidationError, match="review gate"):
        FurnitureSpec.model_validate(approved_spec_dict)


def test_an_approved_spec_with_an_unreviewed_constraint_is_rejected(
    approved_spec_dict: dict[str, Any],
) -> None:
    approved_spec_dict["constraints"][0]["status"] = "needs_review"

    with pytest.raises(ValidationError, match="review gate"):
        FurnitureSpec.model_validate(approved_spec_dict)


def test_an_approved_spec_with_zero_parts_is_rejected(approved_spec_dict: dict[str, Any]) -> None:
    """The zero-part hole: a spec with nothing extracted must not approve —
    approving it would generate an empty cut list."""
    approved_spec_dict["parts"] = []

    with pytest.raises(ValidationError, match="review gate"):
        FurnitureSpec.model_validate(approved_spec_dict)


def test_zero_part_blocker_names_the_problem_and_the_expected_grammar(
    cabinet_spec_dict: dict[str, Any],
) -> None:
    """The draft constructs, but the blocker says nothing was extracted and
    what the parser can read — not just that approval failed."""
    cabinet_spec_dict["parts"] = []
    spec = FurnitureSpec.model_validate(cabinet_spec_dict)

    errors = spec.approval_errors()

    assert any("no parts were extracted" in error for error in errors)
    assert any("SIDE PANEL" in error for error in errors)
