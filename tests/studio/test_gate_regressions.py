"""Gate-semantics regressions through the studio's job-scoped mount.

The review refactor (per-request spec resolution) must not move the gate:
append-only log, question-first queue, approval blockers, and locked-after-
approval all hold through ``/jobs/{job_id}`` exactly as the single-spec
mode's own suite (``tests/review/``) proves them for ``--spec`` runs.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_approval_is_blocked_by_open_questions(studio: TestClient, uploaded_job_id: str) -> None:
    response = studio.post(f"/jobs/{uploaded_job_id}/api/approve")
    assert response.status_code == 409
    assert response.json()["gate_errors"]


def test_corrections_append_to_the_log_and_persist(
    studio: TestClient, uploaded_job_id: str, jobs_dir: Path
) -> None:
    review = studio.get(f"/jobs/{uploaded_job_id}/api/review").json()
    value = review["queue"]["unreviewed"][0]
    address = value["address"]
    response = studio.post(
        f"/jobs/{uploaded_job_id}/api/values",
        json={"address": address, "status": "approved"},
    )
    assert response.status_code == 200

    # The log records the action, in order, and the spec file carries it.
    log = studio.get(f"/jobs/{uploaded_job_id}/api/log").json()
    assert [entry["action"] for entry in log] == ["value_corrected"]
    on_disk = (jobs_dir / uploaded_job_id / "spec.json").read_text(encoding="utf-8")
    assert '"status": "approved"' in on_disk


def test_questions_come_first_in_the_queue(studio: TestClient, uploaded_job_id: str) -> None:
    queue = studio.get(f"/jobs/{uploaded_job_id}/api/review").json()["queue"]
    assert queue["questions"]  # the fixture's unresolvable readings block first
    # Questions stay open until answered: correcting values does not clear
    # them, so approval remains blocked afterwards.
    approval = studio.post(f"/jobs/{uploaded_job_id}/api/approve")
    assert approval.status_code == 409
    remaining = studio.get(f"/jobs/{uploaded_job_id}/api/review").json()
    assert remaining["queue"]["questions"]


def test_approval_locks_corrections(studio: TestClient, uploaded_job_id: str) -> None:
    """Drive a job through the gate, then confirm the lock engages."""
    review = studio.get(f"/jobs/{uploaded_job_id}/api/review").json()

    # Answer every question.
    for _ in review["queue"]["questions"]:
        response = studio.post(
            f"/jobs/{uploaded_job_id}/api/questions/resolve",
            json={"index": 0, "answer": "per the shop drawing, 18mm"},
        )
        assert response.status_code == 200

    # Batch-confirm every value, part, and constraint (addresses walked
    # from the raw spec payload, as the e2e proof does), then approve.
    spec_data = studio.get(f"/jobs/{uploaded_job_id}/api/review").json()["spec"]
    addresses: list[dict[str, object]] = []
    for part in spec_data["parts"]:
        for kind in part["dimensions"]:
            addresses.append({"part_id": part["id"], "feature_index": None, "kind": kind})
        for index, feature in enumerate(part["features"]):
            for kind in feature["dimensions"]:
                addresses.append({"part_id": part["id"], "feature_index": index, "kind": kind})
    confirmed = studio.post(
        f"/jobs/{uploaded_job_id}/api/values/confirm-batch",
        json={
            "addresses": addresses,
            "part_ids": [part["id"] for part in spec_data["parts"]],
            "constraint_indexes": list(range(len(spec_data["constraints"]))),
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["gate_errors"] == []

    approved = studio.post(f"/jobs/{uploaded_job_id}/api/approve")
    assert approved.status_code == 200
    assert approved.json()["spec_status"] == "approved"

    # Locked: a correction now fails the gate.
    locked = studio.get(f"/jobs/{uploaded_job_id}/api/review").json()
    part = locked["spec"]["parts"][0]
    address = {
        "part_id": part["id"],
        "feature_index": None,
        "kind": next(iter(part["dimensions"])),
    }
    denied = studio.post(
        f"/jobs/{uploaded_job_id}/api/values",
        json={"address": address, "value": 123},
    )
    assert denied.status_code == 409
    assert "locked" in denied.json()["gate_errors"][0]
