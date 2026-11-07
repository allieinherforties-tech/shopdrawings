"""Store tests: corrections persist in the spec file; the gate cannot be bypassed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shop_drawings.review.models import ValueAddress
from shop_drawings.review.store import GateError, ReviewStore
from shop_drawings.schema import DimensionKind, ReviewStatus


def _addr(part_id: str, kind: str, feature_index: int | None = None) -> ValueAddress:
    return ValueAddress(part_id=part_id, kind=DimensionKind(kind), feature_index=feature_index)


def test_load_fixture(spec_file: Path) -> None:
    store = ReviewStore(spec_file)
    assert store.spec.project.name == "entry-cabinet-001"
    assert store.spec.status is ReviewStatus.NEEDS_REVIEW


def test_resolve_question_removes_it_and_logs(spec_file: Path) -> None:
    store = ReviewStore(spec_file)
    question = store.spec.unresolved_questions[0]
    store.resolve_question(0, "No — 380mm is the case depth, door sits proud.")

    assert store.spec.unresolved_questions == []
    reloaded = json.loads(spec_file.read_text())
    assert reloaded["unresolved_questions"] == []

    log = store.read_log()
    assert log[-1].action == "question_resolved"
    assert question in log[-1].detail
    assert "case depth" in log[-1].detail


def test_resolve_question_requires_an_answer(spec_file: Path) -> None:
    store = ReviewStore(spec_file)
    with pytest.raises(ValueError, match="answer is required"):
        store.resolve_question(0, "   ")
    assert store.spec.unresolved_questions != []


def test_resolve_question_bad_index(spec_file: Path) -> None:
    store = ReviewStore(spec_file)
    with pytest.raises(IndexError):
        store.resolve_question(99, "anything")


def test_value_correction_persists_in_spec_json(spec_file: Path) -> None:
    store = ReviewStore(spec_file)
    store.correct_value(_addr("door_left", "width"), value=396.0, status=ReviewStatus.APPROVED)

    reloaded = json.loads(spec_file.read_text())
    door = next(p for p in reloaded["parts"] if p["id"] == "door_left")
    dim = door["dimensions"]["width"]
    assert dim["value"] == 396.0
    assert dim["status"] == "approved"
    # Traceability survives the correction: same crop, same source text.
    assert dim["source"]["text"] == "796"


def test_status_change_persists_per_value(spec_file: Path) -> None:
    store = ReviewStore(spec_file)
    store.correct_value(_addr("side_left", "length"), status=ReviewStatus.APPROVED)

    reloaded = json.loads(spec_file.read_text())
    side = next(p for p in reloaded["parts"] if p["id"] == "side_left")
    assert side["dimensions"]["length"]["status"] == "approved"
    untouched = next(p for p in reloaded["parts"] if p["id"] == "bottom_panel")
    assert untouched["dimensions"]["length"]["status"] == "needs_review"


def test_feature_dimension_correction(spec_file: Path) -> None:
    store = ReviewStore(spec_file)
    store.correct_value(_addr("side_left", "setback", feature_index=0), value=37.0)

    reloaded = json.loads(spec_file.read_text())
    feature = next(p for p in reloaded["parts"] if p["id"] == "side_left")["features"][0]
    assert feature["dimensions"]["setback"]["value"] == 37.0


def test_unknown_part_raises_keyerror(spec_file: Path) -> None:
    store = ReviewStore(spec_file)
    with pytest.raises(KeyError):
        store.correct_value(_addr("nope", "width"))


def test_approve_blocked_while_questions_open(spec_file: Path) -> None:
    store = ReviewStore(spec_file)
    with pytest.raises(GateError) as exc_info:
        store.approve()
    assert any("unresolved question" in e for e in exc_info.value.errors)
    assert store.spec.status is ReviewStatus.NEEDS_REVIEW
    assert store.read_log()[-1].action == "approve_rejected"


def test_approve_blocked_while_values_unreviewed(spec_file: Path) -> None:
    store = ReviewStore(spec_file)
    store.resolve_question(0, "Case depth; door is separate.")
    with pytest.raises(GateError) as exc_info:
        store.approve()
    assert any("not approved" in e for e in exc_info.value.errors)
    assert store.spec.status is ReviewStatus.NEEDS_REVIEW


def test_full_path_from_needs_review_to_approved(spec_file: Path) -> None:
    store = ReviewStore(spec_file)
    store.resolve_question(0, "No — 380mm is the case depth.")
    store.correct_value(_addr("door_left", "width"), value=396.0)

    for part in store.spec.parts:
        for kind in part.dimensions:
            store.correct_value(
                ValueAddress(part_id=part.id, kind=kind),
                status=ReviewStatus.APPROVED,
            )
        for index, feature in enumerate(part.features):
            for kind in feature.dimensions:
                store.correct_value(
                    ValueAddress(part_id=part.id, feature_index=index, kind=kind),
                    status=ReviewStatus.APPROVED,
                )
        store.set_part_status(part.id, ReviewStatus.APPROVED)
    for index in range(len(store.spec.constraints)):
        store.set_constraint_status(index, ReviewStatus.APPROVED)

    store.approve()
    assert store.spec.status is ReviewStatus.APPROVED
    assert store.spec.approval_errors() == []

    # The exported spec JSON carries the approval and every correction.
    reloaded = json.loads(spec_file.read_text())
    assert reloaded["status"] == "approved"
    door = next(p for p in reloaded["parts"] if p["id"] == "door_left")
    assert door["dimensions"]["width"]["value"] == 396.0

    # A fresh store reads the approved spec back from disk.
    assert ReviewStore(spec_file).spec.status is ReviewStatus.APPROVED


def test_corrections_locked_after_approval(spec_file: Path) -> None:
    store = ReviewStore(spec_file)
    for part in store.spec.parts:
        for kind in part.dimensions:
            store.correct_value(
                ValueAddress(part_id=part.id, kind=kind),
                status=ReviewStatus.APPROVED,
            )
        for index, feature in enumerate(part.features):
            for kind in feature.dimensions:
                store.correct_value(
                    ValueAddress(part_id=part.id, feature_index=index, kind=kind),
                    status=ReviewStatus.APPROVED,
                )
        store.set_part_status(part.id, ReviewStatus.APPROVED)
    for index in range(len(store.spec.constraints)):
        store.set_constraint_status(index, ReviewStatus.APPROVED)
    store.resolve_question(0, "Case depth.")
    store.approve()

    with pytest.raises(GateError, match="locked"):
        store.correct_value(_addr("side_left", "length"), value=999)
    assert store.spec.parts[0].dimensions[DimensionKind.LENGTH].value == 720


def test_part_status_persists(spec_file: Path) -> None:
    store = ReviewStore(spec_file)
    store.set_part_status("side_left", ReviewStatus.APPROVED)
    reloaded = json.loads(spec_file.read_text())
    side = next(p for p in reloaded["parts"] if p["id"] == "side_left")
    assert side["status"] == "approved"
    assert store.read_log()[-1].action == "part_status_set"


def test_constraint_status_persists(spec_file: Path) -> None:
    store = ReviewStore(spec_file)
    store.set_constraint_status(0, ReviewStatus.APPROVED)
    reloaded = json.loads(spec_file.read_text())
    assert reloaded["constraints"][0]["status"] == "approved"
    assert store.read_log()[-1].action == "constraint_status_set"


def test_unknown_part_and_constraint(spec_file: Path) -> None:
    store = ReviewStore(spec_file)
    with pytest.raises(KeyError):
        store.set_part_status("nope", ReviewStatus.APPROVED)
    with pytest.raises(IndexError):
        store.set_constraint_status(5, ReviewStatus.APPROVED)
