"""The happy path: upload the fixture design PDF, land in its review."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_upload_redirects_to_the_job_s_review(
    studio: TestClient, cabinet_pdf: Path, jobs_dir: Path
) -> None:
    response = studio.post(
        "/api/upload",
        files={"file": ("entry-cabinet.pdf", cabinet_pdf.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/jobs/")
    job_id = response.headers["location"].split("/")[2]
    assert (jobs_dir / job_id / "spec.json").exists()
    assert (jobs_dir / job_id / "entry-cabinet.pdf").exists()


def test_review_serves_that_job_s_spec_questions(studio: TestClient, uploaded_job_id: str) -> None:
    review = studio.get(f"/jobs/{uploaded_job_id}/api/review")
    assert review.status_code == 200
    payload = review.json()
    assert payload["spec_status"] == "needs_review"
    # The fixture's blocked questions render for THIS spec, on the first
    # try — the review UI's queue, not a pre-extracted demo.
    assert payload["queue"]["questions"]
    assert payload["spec"]["project"]["source"] == "entry-cabinet.pdf"


def test_review_page_shell_loads_for_the_job(studio: TestClient, uploaded_job_id: str) -> None:
    response = studio.get(f"/jobs/{uploaded_job_id}/")
    assert response.status_code == 200
    assert "Review Gate" in response.text


def test_review_page_assets_resolve_under_the_job_prefix(
    studio: TestClient, uploaded_job_id: str
) -> None:
    styles = studio.get(f"/jobs/{uploaded_job_id}/static/styles.css")
    assert styles.status_code == 200
    page_image = studio.get(f"/jobs/{uploaded_job_id}/api/page/1/image")
    assert page_image.status_code == 200
    assert "png" in page_image.headers["content-type"]


def test_two_uploads_yield_two_independent_jobs(studio: TestClient, cabinet_pdf: Path) -> None:
    for _ in range(2):
        response = studio.post(
            "/api/upload",
            files={"file": ("entry-cabinet.pdf", cabinet_pdf.read_bytes(), "application/pdf")},
        )
        assert response.status_code == 303
    listing = studio.get("/api/jobs")
    assert listing.status_code == 200
    jobs = listing.json()
    assert len(jobs) == 2
    assert len({job["job_id"] for job in jobs}) == 2  # distinct ids: no shared state
    specs = [
        studio.get(f"/jobs/{job['job_id']}/api/review").json()["spec"]["project"]["source"]
        for job in jobs
    ]
    assert specs == ["entry-cabinet.pdf", "entry-cabinet.pdf"]


def test_landing_page_serves(studio: TestClient) -> None:
    response = studio.get("/")
    assert response.status_code == 200
    assert "Upload a design" in response.text
