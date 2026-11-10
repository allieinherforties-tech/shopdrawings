"""Negative paths: the four fabrication-unsafe inputs, each blocked at its layer.

These are the pipeline's named failure modes from the blueprint's §5 failure
table, proven in code. Each test blocks one input at the layer that owns it:

1. An untraceable value is unconstructible at the **schema** layer.
2. Approval with an open question is refused by the **review gate** — the
   schema refuses the approved state, the store refuses the approve action,
   and the API returns 409 with the gate message.
3. A failing constraint-closure check blocks the approval decision at the
   **validation** layer, with both values shown.
4. A not-approved spec never reaches geometry — the **generation** layer
   refuses before writing anything, including against a forged spec object.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from shop_drawings import FurnitureSpec
from shop_drawings.generation import generate
from shop_drawings.generation.errors import NotApprovedError
from shop_drawings.review.app import create_app
from shop_drawings.review.store import GateError, ReviewStore
from shop_drawings.schema import ReviewStatus
from shop_drawings.validation import approval_blockers, validate_spec
from shop_drawings.validation.report import RuleId

# ---------------------------------------------------------------------------
# 1. Untraceable value — blocked at the schema layer
# ---------------------------------------------------------------------------


def test_untraceable_value_is_unconstructible(cabinet_spec_dict: dict[str, Any]) -> None:
    """A dimension without a source reference cannot exist — even by hand."""
    untraceable = copy.deepcopy(cabinet_spec_dict)
    del untraceable["parts"][0]["dimensions"]["length"]["source"]
    with pytest.raises(ValidationError) as exc_info:
        FurnitureSpec.model_validate(untraceable)
    # The schema names the field it refused: no source, no value, full stop.
    assert "source" in str(exc_info.value)
    assert "Field required" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. Unresolved question on approval — blocked at the review gate
# ---------------------------------------------------------------------------


@pytest.fixture
def approved_but_questioned_dict(approved_spec_dict: dict[str, Any]) -> dict[str, Any]:
    """The fully approved cabinet, but with one question left open."""
    data = copy.deepcopy(approved_spec_dict)
    data["unresolved_questions"] = ["Confirm whether the 380mm depth includes the door front."]
    return data


@pytest.fixture
def questioned_draft_dict(cabinet_spec_dict: dict[str, Any]) -> dict[str, Any]:
    """The draft cabinet, reduced to one open question so the gate message is exact."""
    data = copy.deepcopy(cabinet_spec_dict)
    data["unresolved_questions"] = ["Confirm whether the 380mm depth includes the door front."]
    return data


def test_schema_refuses_the_approved_state_with_an_open_question(
    approved_but_questioned_dict: dict[str, Any],
) -> None:
    """The schema-level half of the gate: marked approved + open question = invalid."""
    with pytest.raises(ValueError, match="fails the review gate") as exc_info:
        FurnitureSpec.model_validate(approved_but_questioned_dict)
    assert "1 unresolved question(s) must be resolved first" in str(exc_info.value)


def test_store_refuses_approval_with_an_open_question(
    questioned_draft_dict: dict[str, Any], tmp_path: Path
) -> None:
    """The review store refuses the approve action while a question is open."""
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(questioned_draft_dict, indent=2), encoding="utf-8")
    store = ReviewStore(spec_path)
    with pytest.raises(GateError) as exc_info:
        store.approve()
    assert "unresolved question(s) must be resolved first" in str(exc_info.value)
    # Nothing was approved: the file still carries the draft status.
    stored = json.loads(spec_path.read_text(encoding="utf-8"))
    assert stored.get("status") != "approved"


def test_api_returns_409_on_blocked_approval(
    questioned_draft_dict: dict[str, Any], tmp_path: Path
) -> None:
    """The HTTP API reports the gate block instead of approving."""
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(questioned_draft_dict, indent=2), encoding="utf-8")
    client = TestClient(create_app(spec_path))
    response = client.post("/api/approve")
    assert response.status_code == 409
    gate_errors = response.json()["gate_errors"]
    assert any("unresolved question(s)" in message for message in gate_errors)


# ---------------------------------------------------------------------------
# 3. Failing closure check — blocked at the validation layer
# ---------------------------------------------------------------------------


def test_failing_closure_blocks_with_both_values(
    approved_spec_dict: dict[str, Any],
) -> None:
    """Part extents that cannot sum to the stated overall width block the
    approval decision, with the stated value and the closest achievable sum
    both shown. The §3 example's only part is the 720×380×18 side panel, so
    no selection of its extents can reach a 900 mm overall."""
    broken = copy.deepcopy(approved_spec_dict)
    width = next(
        constraint for constraint in broken["constraints"] if constraint["type"] == "overall_width"
    )
    width["value"] = 900  # the single side panel cannot make 900 (closest: 720)

    spec = FurnitureSpec.model_validate(broken)
    report = validate_spec(spec)
    closure = [issue for issue in report.issues if issue.rule == RuleId.CONSTRAINT_CLOSURE]
    assert closure, "closure failure must be reported"
    message = closure[0].message
    assert "900 mm" in message, "the stated overall width must appear"
    assert "closest achievable sum is" in message
    assert "720 mm" in message, "the achievable sum must appear beside the stated value"

    blockers = approval_blockers(spec)
    assert any("closest achievable sum is" in issue.message for issue in blockers), (
        "the failed closure check must block the approval decision"
    )


# ---------------------------------------------------------------------------
# 4. Not-approved spec — blocked at the generation layer
# ---------------------------------------------------------------------------


def test_needs_review_spec_never_reaches_geometry(
    approved_spec_dict: dict[str, Any], tmp_path: Path
) -> None:
    """A needs_review spec is refused before anything is written."""
    draft_data = copy.deepcopy(approved_spec_dict)
    draft_data["status"] = "needs_review"
    draft = FurnitureSpec.model_validate(draft_data)

    out_dir = tmp_path / "fabrication"
    with pytest.raises(NotApprovedError) as exc_info:
        generate(draft, out_dir=out_dir)
    assert "needs_review, not approved" in str(exc_info.value)
    assert "the review gate blocks generation" in str(exc_info.value)
    assert not out_dir.exists() or not any(out_dir.iterdir()), (
        "a refused spec must leave no files behind"
    )


def test_forged_approved_spec_is_still_blocked(
    approved_spec: FurnitureSpec, tmp_path: Path
) -> None:
    """Even a spec object forged past the schema (constructed in memory with an
    open question) is refused by the generator's own gate check."""
    forged = approved_spec.model_copy(
        update={"unresolved_questions": ["Someone forged this in after approval."]}
    )
    assert forged.status == ReviewStatus.APPROVED
    with pytest.raises(NotApprovedError) as exc_info:
        generate(forged, out_dir=tmp_path / "fabrication")
    assert "fails the review gate" in str(exc_info.value)


def test_forged_zero_part_spec_is_still_blocked(
    approved_spec: FurnitureSpec, tmp_path: Path
) -> None:
    """A zero-part spec that bypassed validation (``model_copy`` skips the
    schema's constructor gate) is refused by the generator too: an approved
    spec with nothing in it would only produce an empty cut list."""
    forged = approved_spec.model_copy(update={"parts": []})
    assert forged.status == ReviewStatus.APPROVED
    with pytest.raises(NotApprovedError) as exc_info:
        generate(forged, out_dir=tmp_path / "fabrication")
    assert "no parts were extracted" in str(exc_info.value)
    out_dir = tmp_path / "fabrication"
    assert not out_dir.exists() or not any(out_dir.iterdir()), (
        "a refused spec must leave no files behind"
    )
