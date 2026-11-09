"""The engine: one call between extraction and the review gate.

Maps the blueprint §5 failure-mode table onto the checks: silent inference
of hidden construction and unreviewed values block via the schema's gate
(surfaced, not reimplemented); the 720-vs-800 closure conflict blocks with
both values; an approved-but-inconsistent spec is still caught.
"""

from typing import Any

from shop_drawings import FurnitureSpec
from shop_drawings.validation import (
    RuleId,
    ValidationReport,
    approval_blockers,
    validate_spec,
)


def test_a_fully_valid_approved_spec_has_no_blockers(cabinet: dict[str, Any]) -> None:
    spec = FurnitureSpec.model_validate(cabinet)

    assert approval_blockers(spec) == []


def test_the_blueprint_draft_cabinet_blocks_with_its_conflict(
    cabinet_spec_dict: dict[str, Any],
) -> None:
    """The §3 example: 720/380/18 parts against an 800 mm overall — and an
    unresolved question. Both kinds of blocker appear in one queue."""
    spec = FurnitureSpec.model_validate(cabinet_spec_dict)

    blockers = approval_blockers(spec)

    rules = {issue.rule for issue in blockers}
    assert RuleId.REVIEW_GATE in rules  # the schema's gate, quoted verbatim
    assert RuleId.CONSTRAINT_CLOSURE in rules
    messages = " | ".join(issue.message for issue in blockers)
    assert "unresolved" in messages
    assert "800" in messages


def test_an_approved_but_inconsistent_spec_is_still_caught(make_spec: Any, constraint: Any) -> None:
    """Defense in depth: the schema gate checks review status, not geometry.
    A spec can pass the gate and still fail closure — validation must catch it
    before generation, not after."""
    spec_dict = make_spec(
        constraints=[constraint("overall_width", 800)],
    )
    spec_dict["parts"][1]["dimensions"]["width"]["value"] = 700
    spec = FurnitureSpec.model_validate(spec_dict)

    assert spec.approval_errors() == []  # the gate alone would let it through
    assert validate_spec(spec).blocking  # the validation layer does not


def test_gate_errors_surface_without_reimplementation(cabinet_spec_dict: dict[str, Any]) -> None:
    spec = FurnitureSpec.model_validate(cabinet_spec_dict)

    gate_only = [
        issue.message for issue in approval_blockers(spec) if issue.rule is RuleId.REVIEW_GATE
    ]
    assert gate_only == spec.approval_errors()


def test_the_report_round_trips_through_json(cabinet: dict[str, Any]) -> None:
    spec_dict = dict(cabinet)
    spec_dict["parts"][1]["dimensions"]["width"]["value"] = 700
    report = validate_spec(FurnitureSpec.model_validate(spec_dict))

    encoded = report.model_dump_json()
    reloaded = ValidationReport.model_validate_json(encoded)

    assert reloaded == report
    assert reloaded.blocking
