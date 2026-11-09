"""API tests: the review flow over HTTP, gate rejections included."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from shop_drawings.review.app import create_app


def all_value_addresses(spec_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every dimension address in a spec payload, as JSON-shaped dicts."""
    addresses: list[dict[str, Any]] = []
    for part in spec_data["parts"]:
        for kind in part["dimensions"]:
            addresses.append({"part_id": part["id"], "feature_index": None, "kind": kind})
        for index, feature in enumerate(part["features"]):
            for kind in feature["dimensions"]:
                addresses.append({"part_id": part["id"], "feature_index": index, "kind": kind})
    return addresses


def confirm_everything_payload(spec_data: dict[str, Any]) -> dict[str, Any]:
    """A batch-confirm body that approves every value, part, and constraint."""
    return {
        "addresses": all_value_addresses(spec_data),
        "part_ids": [p["id"] for p in spec_data["parts"]],
        "constraint_indexes": list(range(len(spec_data["constraints"]))),
    }


def _client(spec_file: Path) -> TestClient:
    return TestClient(create_app(spec_file))


def _review(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/review")
    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    return payload


def test_index_serves_html(spec_file: Path) -> None:
    response = _client(spec_file).get("/")
    assert response.status_code == 200
    assert "Review Gate" in response.text


def test_review_payload_has_priority_order(spec_file: Path) -> None:
    payload = _review(_client(spec_file))
    queue = payload["queue"]
    # Blueprint §4 order: questions, conflicts, low confidence, then the rest.
    assert len(queue["questions"]) == 1
    assert any(c["addresses"][0]["part_id"] == "door_left" for c in queue["conflicts"])
    low = queue["low_confidence"]
    assert all(v["confidence"] <= 0.85 for v in low)
    assert low[0]["confidence"] <= low[-1]["confidence"]
    assert all(v["confidence"] > 0.85 for v in queue["unreviewed"])
    assert payload["gate_errors"]  # nothing is approved yet
    assert payload["spec_status"] == "needs_review"


def test_page_image_is_svg_placeholder(spec_file: Path) -> None:
    response = _client(spec_file).get("/api/page/1/image")
    assert response.status_code == 200
    assert "svg" in response.headers["content-type"]
    assert "796" in response.text  # the extracted text is drawn at its region


def test_review_payload_lists_parts_and_constraints(spec_file: Path) -> None:
    payload = _review(_client(spec_file))
    queue = payload["queue"]
    assert [p["part_id"] for p in queue["parts"]] == [
        "side_left",
        "bottom_panel",
        "door_left",
    ]
    assert queue["constraints"][0]["type"] == "overall_width"
    assert queue["constraints"][0]["value"] == 800


def test_part_and_constraint_status_endpoints(spec_file: Path) -> None:
    client = _client(spec_file)
    ok = client.post("/api/parts", json={"part_id": "side_left", "status": "approved"})
    assert ok.status_code == 200
    assert ok.json()["queue"]["parts"] == [
        {"part_id": "bottom_panel", "status": "needs_review"},
        {"part_id": "door_left", "status": "needs_review"},
    ]

    ok = client.post("/api/constraints", json={"index": 0, "status": "approved"})
    assert ok.status_code == 200
    assert ok.json()["queue"]["constraints"] == []

    missing = client.post("/api/parts", json={"part_id": "nope", "status": "approved"})
    assert missing.status_code == 404
    bad = client.post("/api/constraints", json={"index": 5, "status": "approved"})
    assert bad.status_code == 404


def test_correction_persists_to_spec_file(spec_file: Path) -> None:
    client = _client(spec_file)
    response = client.post(
        "/api/values",
        json={
            "address": {
                "part_id": "door_left",
                "feature_index": None,
                "kind": "width",
            },
            "value": 396,
            "status": "approved",
        },
    )
    assert response.status_code == 200

    reloaded = json.loads(spec_file.read_text())
    door = next(p for p in reloaded["parts"] if p["id"] == "door_left")
    assert door["dimensions"]["width"]["value"] == 396
    assert door["dimensions"]["width"]["status"] == "approved"


def test_batch_confirm(spec_file: Path) -> None:
    client = _client(spec_file)
    # The open question is answered first, as a reviewer would.
    client.post("/api/questions/resolve", json={"index": 0, "answer": "Case depth."})
    body = confirm_everything_payload(_review(client)["spec"])
    response = client.post("/api/values/confirm-batch", json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["queue"]["approved_values"] == payload["queue"]["total_values"]
    assert payload["queue"]["low_confidence"] == []
    assert payload["queue"]["unreviewed"] == []
    assert payload["queue"]["parts"] == []
    assert payload["queue"]["constraints"] == []
    assert payload["gate_errors"] == []


def test_resolve_question_api(spec_file: Path) -> None:
    client = _client(spec_file)
    response = client.post(
        "/api/questions/resolve",
        json={"index": 0, "answer": "380mm is the case depth."},
    )
    assert response.status_code == 200
    assert response.json()["queue"]["questions"] == []


def test_resolve_question_requires_answer(spec_file: Path) -> None:
    response = _client(spec_file).post("/api/questions/resolve", json={"index": 0, "answer": "  "})
    assert response.status_code == 422  # schema-level: min_length=1


def test_unknown_part_is_404(spec_file: Path) -> None:
    response = _client(spec_file).post(
        "/api/values",
        json={
            "address": {"part_id": "nope", "feature_index": None, "kind": "width"},
            "status": "approved",
        },
    )
    assert response.status_code == 404


def test_approve_rejected_409_with_gate_errors(spec_file: Path) -> None:
    response = _client(spec_file).post("/api/approve")
    assert response.status_code == 409
    body = response.json()
    assert any("unresolved question" in e for e in body["gate_errors"])
    # And the spec on disk is untouched (the fixture omits `status`, which
    # defaults to needs_review).
    assert json.loads(spec_file.read_text()).get("status", "needs_review") == "needs_review"


def test_full_review_flow_to_approved(spec_file: Path) -> None:
    client = _client(spec_file)

    resolved = client.post(
        "/api/questions/resolve",
        json={"index": 0, "answer": "380mm is the case depth; door sits proud."},
    )
    assert resolved.status_code == 200

    body = confirm_everything_payload(_review(client)["spec"])
    confirmed = client.post("/api/values/confirm-batch", json=body)
    assert confirmed.status_code == 200
    assert confirmed.json()["gate_errors"] == []

    approved = client.post("/api/approve")
    assert approved.status_code == 200
    assert approved.json()["spec_status"] == "approved"

    exported = json.loads(spec_file.read_text())
    assert exported["status"] == "approved"
    assert exported["unresolved_questions"] == []


def test_corrections_locked_after_approval(spec_file: Path) -> None:
    client = _client(spec_file)
    client.post("/api/questions/resolve", json={"index": 0, "answer": "Case depth."})
    body = confirm_everything_payload(_review(client)["spec"])
    client.post("/api/values/confirm-batch", json=body)
    assert client.post("/api/approve").status_code == 200

    locked = client.post(
        "/api/values",
        json={
            "address": {
                "part_id": "side_left",
                "feature_index": None,
                "kind": "length",
            },
            "value": 999,
        },
    )
    assert locked.status_code == 409


def test_review_only_mode_has_no_vision_record(spec_file: Path) -> None:
    client = TestClient(create_app(spec_file))
    assert client.get("/api/vision").status_code == 404


def test_vision_record_serves_when_a_path_is_given(spec_file: Path, tmp_path: Path) -> None:
    vision = tmp_path / "vision.json"
    vision.write_text(
        '{"enabled": true, "error": null, "observations": [{"text": "DEPTH 380",'
        ' "page": 1, "region": [0, 0, 1, 0.2], "role": "depth",'
        ' "part_label": null, "confidence": 0.7, "outcome": "upgraded"}]}',
        encoding="utf-8",
    )
    client = TestClient(create_app(spec_file, vision_path=vision))
    response = client.get("/api/vision")
    assert response.status_code == 200
    payload = response.json()
    assert payload["observations"][0]["outcome"] == "upgraded"


def test_unreadable_vision_record_is_reported_absent(spec_file: Path, tmp_path: Path) -> None:
    vision = tmp_path / "vision.json"
    vision.write_text("{not json", encoding="utf-8")
    client = TestClient(create_app(spec_file, vision_path=vision))
    assert client.get("/api/vision").status_code == 404
