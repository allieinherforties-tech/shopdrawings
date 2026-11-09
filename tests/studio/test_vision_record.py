"""The vision record: written per job, served to the review UI, degraded safely.

All clients here are stubs — no test touches the network or a key.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from shop_drawings.extraction import DesignDocument, VisionCallout
from shop_drawings.extraction.vision import VisionError
from shop_drawings.studio.app import create_studio_app


class StubVisionClient:
    """Canned callouts — no network."""

    def __init__(self, *callouts: VisionCallout) -> None:
        self._callouts = callouts

    def extract(self, document: DesignDocument) -> tuple[VisionCallout, ...]:
        return self._callouts


class FailingVisionClient:
    """The API is down — extraction must still produce a spec."""

    def extract(self, document: DesignDocument) -> tuple[VisionCallout, ...]:
        raise VisionError("anthropic: api error 500 (overloaded)")


def _upload(client: TestClient, cabinet_pdf: Path) -> str:
    response = client.post(
        "/api/upload",
        files={"file": ("entry-cabinet.pdf", cabinet_pdf.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 303, response.text
    return response.headers["location"].split("/")[2]


def test_vision_observations_are_served_to_the_review_page(
    tmp_path: Path, cabinet_pdf: Path
) -> None:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    client = TestClient(
        create_studio_app(
            jobs,
            vision_client=StubVisionClient(
                # Re-reads the text layer's own "DEPTH 380" line.
                VisionCallout(
                    text="DEPTH 380",
                    page=1,
                    region=(0.0, 0.0, 1.0, 1.0),
                    role="depth",
                    part_label=None,
                    confidence=0.70,
                ),
                # The model itself doubts it → question, never a value.
                VisionCallout(
                    text="700",
                    page=1,
                    region=(0.40, 0.40, 0.50, 0.45),
                    role="width",
                    part_label=None,
                    confidence=0.30,
                ),
            ),
        ),
        follow_redirects=False,
    )
    job_id = _upload(client, cabinet_pdf)

    response = client.get(f"/jobs/{job_id}/api/vision")
    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["error"] is None
    outcomes = {o["text"]: o["outcome"] for o in payload["observations"]}
    assert outcomes["DEPTH 380"] == "upgraded"
    assert outcomes["700"] == "question"

    # The review page ships the panel that renders the record.
    page = client.get(f"/jobs/{job_id}/")
    assert page.status_code == 200
    assert 'id="sec-vision"' in page.text
    app_js = client.get(f"/jobs/{job_id}/static/app.js")
    assert app_js.status_code == 200
    assert "renderVision" in app_js.text


def test_vision_off_reports_disabled_not_absent(studio: TestClient, uploaded_job_id: str) -> None:
    response = studio.get(f"/jobs/{uploaded_job_id}/api/vision")
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "error": None, "observations": []}


def test_failing_vision_degrades_and_still_uploads(tmp_path: Path, cabinet_pdf: Path) -> None:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    client = TestClient(
        create_studio_app(jobs, vision_client=FailingVisionClient()),
        follow_redirects=False,
    )
    job_id = _upload(client, cabinet_pdf)
    assert (jobs / job_id / "spec.json").exists()
    payload = client.get(f"/jobs/{job_id}/api/vision").json()
    assert payload["enabled"] is True
    assert "VisionError" in payload["error"]
