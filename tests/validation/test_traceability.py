"""Traceability: no untraceable value reaches review.

The schema makes a value without a source reference unconstructible; these
tests pin that contract and cover what can still go wrong in a constructed
spec — a source pointing nowhere, a part with no evidence at all.
"""

from typing import Any

import pytest

from shop_drawings import Dimension, FurnitureSpec
from shop_drawings.validation import RuleId, validate_spec


def traceability_issues(spec_dict: dict[str, Any]) -> list[str]:
    report = validate_spec(FurnitureSpec.model_validate(spec_dict))
    return [issue.message for issue in report.issues if issue.rule is RuleId.TRACEABILITY]


def test_a_dimension_without_a_source_is_unconstructible() -> None:
    with pytest.raises(Exception, match="source"):
        Dimension(value=720, confidence=0.9)  # type: ignore[call-arg]


def test_every_value_in_a_valid_spec_is_traced(cabinet: dict[str, Any]) -> None:
    assert traceability_issues(cabinet) == []


def test_a_part_with_no_evidence_blocks(make_spec: Any) -> None:
    orphan = {
        "id": "mystery_panel",
        "type": "panel",
        "quantity": 1,
        "material": "?",
        "dimensions": {},
        "features": [],
        "edge_banding": [],
        "source_references": [],
        "status": "approved",
    }
    spec = make_spec(parts=[orphan])

    issues = traceability_issues(spec)

    assert any("mystery_panel" in issue and "no dimensions" in issue for issue in issues)


def test_a_source_pointing_nowhere_blocks() -> None:
    """Whitespace-only text names no dimension — the value is untraceable.

    (A missing source, page 0, and a zero-area region are all unconstructible;
    the schema rejects them. Whitespace text slips through ``min_length=1``,
    which is exactly what this second check exists to catch.)
    """
    spec_dict = {
        "schema_version": 1,
        "project": {"name": "p", "units": "mm", "source": "design.pdf"},
        "parts": [
            {
                "id": "panel",
                "type": "panel",
                "material": "ply",
                "dimensions": {
                    "length": {
                        "value": 720,
                        "source": {"page": 1, "region": [0.1, 0.1, 0.2, 0.2], "text": "  "},
                        "confidence": 0.9,
                    }
                },
            }
        ],
        "status": "needs_review",
    }

    issues = traceability_issues(spec_dict)

    assert any("malformed source reference" in issue for issue in issues)
