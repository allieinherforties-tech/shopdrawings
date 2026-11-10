"""The approve → generate → download route, through the studio front door.

Approving a job (via the mounted review API, exactly as the browser does)
must run the generation pipeline, store its outputs under the job's
``outputs/`` directory, and surface download links — in the approve
response's payload, on the outputs page, and through the download
endpoint. Nothing here stubs the pipeline: the files on disk are the
assertion site.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from shop_drawings.studio import jobs as jobs_module

pytestmark = pytest.mark.usefixtures("uploaded_job_id")


def _review_url(job_id: str, path: str) -> str:
    return f"/jobs/{job_id}/{path}"


def _all_value_addresses(spec_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every dimension address in a spec payload, as JSON-shaped dicts."""
    addresses: list[dict[str, Any]] = []
    for part in spec_data["parts"]:
        for kind in part["dimensions"]:
            addresses.append({"part_id": part["id"], "feature_index": None, "kind": kind})
        for index, feature in enumerate(part["features"]):
            for kind in feature["dimensions"]:
                addresses.append({"part_id": part["id"], "feature_index": index, "kind": kind})
    return addresses


def approve_job(studio: TestClient, job_id: str) -> dict[str, Any]:
    """Drive the review gate to approval through the mounted API."""
    while True:
        review = studio.get(_review_url(job_id, "api/review"))
        assert review.status_code == 200, review.text
        queue = review.json()["queue"]
        if not queue["questions"]:
            break
        response = studio.post(
            _review_url(job_id, "api/questions/resolve"),
            json={"index": 0, "answer": "Confirmed against the drawing."},
        )
        assert response.status_code == 200, response.text

    spec_data = studio.get(_review_url(job_id, "api/review")).json()["spec"]
    response = studio.post(
        _review_url(job_id, "api/values/confirm-batch"),
        json={
            "addresses": _all_value_addresses(spec_data),
            "part_ids": [part["id"] for part in spec_data["parts"]],
            "constraint_indexes": list(range(len(spec_data["constraints"]))),
        },
    )
    assert response.status_code == 200, response.text
    approved = studio.post(_review_url(job_id, "api/approve"))
    assert approved.status_code == 200, approved.text
    approve_payload: dict[str, Any] = approved.json()
    return approve_payload


def test_review_payload_has_no_outputs_before_approval(
    studio: TestClient, uploaded_job_id: str
) -> None:
    """Pre-approval, the UI is told there is nothing to download."""
    payload = studio.get(_review_url(uploaded_job_id, "api/review")).json()
    assert payload["outputs"] == []


def test_outputs_page_before_approval_explains_the_state(
    studio: TestClient, uploaded_job_id: str
) -> None:
    response = studio.get(f"/jobs/{uploaded_job_id}/outputs")
    assert response.status_code == 200
    assert "No fabrication outputs yet" in response.text


def _check_names(record: dict[str, Any]) -> list[str]:
    """The check names in a generation record, tolerantly."""
    checks = record.get("checks")
    if not isinstance(checks, list):
        return []
    return [str(entry["name"]) for entry in checks if isinstance(entry, dict)]


def test_approval_runs_generation_and_answers_with_download_links(
    studio: TestClient, uploaded_job_id: str, jobs_dir: Path
) -> None:
    """The approve response itself carries the links; outputs land on disk."""
    payload = approve_job(studio, uploaded_job_id)

    outputs_dir = jobs_dir / uploaded_job_id / "outputs"
    prefix = f"/jobs/{uploaded_job_id}/outputs/"
    linked = {unquote(entry["url"].removeprefix(prefix)) for entry in payload["outputs"]}
    on_disk = {
        path.relative_to(outputs_dir).as_posix()
        for path in outputs_dir.rglob("*")
        if path.is_file()
    }
    assert linked == on_disk
    assert {entry["label"].split(" ")[0] for entry in payload["outputs"]} >= {"3D", "Cut"}
    assert all(entry["url"].startswith(prefix) for entry in payload["outputs"])

    record = jobs_module.read_generation_record(jobs_module.JobPaths(jobs_dir, uploaded_job_id))
    assert record is not None
    assert record["status"] == "generated"
    check_names = _check_names(record)
    assert "roundtrip_reproducibility" in check_names and "dxf_fabrication_checks" in check_names


def test_generated_outputs_reparse_from_disk(
    studio: TestClient, uploaded_job_id: str, jobs_dir: Path
) -> None:
    """The stored .3dm and DXF are real: read back and counted, not assumed."""
    approve_job(studio, uploaded_job_id)
    from ezdxf.filemanagement import readfile

    from tests.generation._builders import objects_of, read_model

    outputs_dir = jobs_dir / uploaded_job_id / "outputs"
    spec = json.loads((jobs_dir / uploaded_job_id / "spec.json").read_text())

    # Parts that resolved get named breps; escalated parts get packages instead.
    model_path = next(outputs_dir.glob("*.3dm"))
    model = read_model(model_path)
    objects = objects_of(model)
    escalated = {
        entry["part_id"]
        for entry in json.loads((jobs_dir / uploaded_job_id / "generation.json").read_text())[
            "escalations"
        ]
    }
    modeled = {part["id"] for part in spec["parts"] if part["type"] != "hardware"} - escalated
    assert {obj.Attributes.Name for obj in objects} >= modeled

    sheet_path = next(outputs_dir.glob("*.dxf"))
    doc = readfile(str(sheet_path))
    layer_names = {layer.dxf.name for layer in doc.layers}
    assert {"CUT", "POCKET", "DRILL", "SHEET"} <= layer_names


def test_download_endpoint_serves_listed_files(
    studio: TestClient, uploaded_job_id: str, jobs_dir: Path
) -> None:
    approve_job(studio, uploaded_job_id)
    outputs_dir = jobs_dir / uploaded_job_id / "outputs"

    model_path = next(outputs_dir.glob("*.3dm"))
    response = studio.get(f"/jobs/{uploaded_job_id}/outputs/{model_path.name}")
    assert response.status_code == 200
    assert response.content == model_path.read_bytes()
    assert "attachment" in response.headers["content-disposition"]

    cutlist_path = next(outputs_dir.glob("*cutlist.csv"))
    response = studio.get(f"/jobs/{uploaded_job_id}/outputs/{cutlist_path.name}")
    assert response.status_code == 200
    assert b"part_id" in response.content

    escalation = next(outputs_dir.rglob("package.json"))
    quoted = escalation.relative_to(outputs_dir).as_posix().replace("/", "%2F")
    response = studio.get(f"/jobs/{uploaded_job_id}/outputs/{quoted}")
    assert response.status_code == 200


def test_download_endpoint_never_serves_unlisted_paths(
    studio: TestClient, uploaded_job_id: str
) -> None:
    """Only generation-record names are served — traversal gets a 404."""
    approve_job(studio, uploaded_job_id)
    for probe in ("../spec.json", "..%2Fspec.json", "spec.json", "no-such-file.dxf"):
        response = studio.get(f"/jobs/{uploaded_job_id}/outputs/{probe}")
        assert response.status_code == 404, probe


def test_outputs_page_after_approval_lists_downloads(
    studio: TestClient, uploaded_job_id: str
) -> None:
    approve_job(studio, uploaded_job_id)
    response = studio.get(f"/jobs/{uploaded_job_id}/outputs")
    assert response.status_code == 200
    assert "3D model" in response.text and "download" in response.text


def test_generation_failure_is_recorded_and_never_a_crash(
    studio: TestClient,
    uploaded_job_id: str,
    jobs_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pipeline failure is the reviewer's outcome, recorded — not a 500."""

    def explode(paths: Any, spec: Any) -> dict[str, Any]:
        raise RuntimeError("dxf writer exploded")

    monkeypatch.setattr(jobs_module, "run_generation", explode)
    approve_job(studio, uploaded_job_id)

    record = jobs_module.read_generation_record(jobs_module.JobPaths(jobs_dir, uploaded_job_id))
    assert record is not None and record["status"] == "failed"
    assert "dxf writer exploded" in str(record["error"])
    assert not (jobs_dir / uploaded_job_id / "outputs").exists()

    page = studio.get(f"/jobs/{uploaded_job_id}/outputs")
    assert page.status_code == 200
    assert "Generation failed" in page.text


def test_outputs_of_an_unknown_job_are_404(studio: TestClient) -> None:
    assert studio.get("/jobs/no-such-job/outputs").status_code == 404
    assert studio.get("/jobs/no-such-job/outputs/model.3dm").status_code == 404
