"""Failure UX: extraction failure is an explained state, never a crash."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_failed_extraction_lands_on_explanation_page(studio: TestClient) -> None:
    """Garbage bytes in a .pdf wrapper fail extraction with a real page."""
    response = studio.post(
        "/api/upload",
        files={"file": ("broken.pdf", b"%PDF-not-really-a-pdf", "application/pdf")},
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/failed/")
    page = studio.get(location)
    assert page.status_code == 200
    assert "Extraction failed" in page.text
    assert "broken.pdf" in page.text  # which file failed
    assert "PdfminerException" in page.text  # what failed, verbatim
    assert "panel-and-case" in page.text  # what to do about it
    assert "Traceback" not in page.text


def test_failed_job_stays_listed(studio: TestClient) -> None:
    response = studio.post(
        "/api/upload",
        files={"file": ("broken.pdf", b"%PDF-not-really-a-pdf", "application/pdf")},
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/failed/")
    listing = studio.get("/api/jobs").json()
    assert len(listing) == 1
    assert listing[0]["status"] == "failed"
    assert listing[0]["source_name"] == "broken.pdf"
    assert listing[0]["error"]


def test_failed_job_has_no_review(studio: TestClient) -> None:
    """A failed job can't be opened in review — 404, not a crash."""
    response = studio.post(
        "/api/upload",
        files={"file": ("broken.pdf", b"%PDF-not-really-a-pdf", "application/pdf")},
    )
    job_id = response.headers["location"].split("/")[2]
    review = studio.get(f"/jobs/{job_id}/api/review")
    assert review.status_code == 404


def test_unknown_job_is_a_404(studio: TestClient) -> None:
    assert studio.get("/jobs/does-not-exist/api/review").status_code == 404
    assert studio.get("/failed/does-not-exist").status_code == 200  # page explains itself


def test_no_slash_job_url_redirects(studio: TestClient, uploaded_job_id: str) -> None:
    response = studio.get(f"/jobs/{uploaded_job_id}")
    assert response.status_code == 307
    assert response.headers["location"] == f"/jobs/{uploaded_job_id}/"
