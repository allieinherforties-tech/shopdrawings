"""Conflict grouping: values claiming one role surface together, never silently."""

from typing import Any

from shop_drawings import FurnitureSpec
from shop_drawings.validation import RuleId, validate_spec


def conflict_issues(spec_dict: dict[str, Any]) -> list[Any]:
    report = validate_spec(FurnitureSpec.model_validate(spec_dict))
    return [issue for issue in report.issues if issue.rule is RuleId.CONFLICT]


def test_two_constraints_claiming_one_role_block_with_both_values(
    make_spec: Any, constraint: Any
) -> None:
    spec = make_spec(
        constraints=[
            constraint("overall_width", 800, text="800"),
            constraint("overall_width", 780, text="780"),
        ]
    )

    issues = conflict_issues(spec)

    assert len(issues) == 1
    message = issues[0].message
    assert "800" in message and "780" in message
    # Both claims keep their source crops — the reviewer sees where each came from.
    sources = [evidence.source for evidence in issues[0].evidence if evidence.source]
    assert {source.text for source in sources} == {"800", "780"}


def test_agreeing_duplicates_do_not_block(make_spec: Any, constraint: Any) -> None:
    spec = make_spec(
        constraints=[
            constraint("overall_width", 800, text="800"),
            constraint("overall_width", 799, text="799", tolerance=1),
        ]
    )

    assert conflict_issues(spec) == []


def test_distinct_roles_never_conflict(make_spec: Any, constraint: Any) -> None:
    spec = make_spec(
        constraints=[constraint("overall_width", 800), constraint("overall_height", 800)]
    )

    assert conflict_issues(spec) == []


def test_duplicate_part_ids_block(make_spec: Any) -> None:
    spec = make_spec()
    spec["parts"].append(dict(spec["parts"][1]))

    issues = conflict_issues(spec)

    assert len(issues) == 1
    assert "'bottom'" in issues[0].message
    assert "one id must mean one part" in issues[0].message


def test_unique_part_ids_do_not_block(cabinet: dict[str, Any]) -> None:
    assert conflict_issues(cabinet) == []
