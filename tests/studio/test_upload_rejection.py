"""Rejection cases: friendly inline errors, no job created, no traceback."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_rejects_disallowed_type(studio: TestClient, jobs_dir: Path) -> None:
    response = studio.post(
        "/api/upload",
        files={"file": ("notes.txt", b"just some words", "text/plain")},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "PDF" in detail  # names what IS accepted
    assert list(jobs_dir.iterdir()) == []  # no job directory created


def test_rejects_oversized_file(studio: TestClient, jobs_dir: Path) -> None:
    oversized = b"x" * (25 * 1024 * 1024 + 1)
    response = studio.post(
        "/api/upload",
        files={"file": ("huge.pdf", oversized, "application/pdf")},
    )
    assert response.status_code == 422
    assert "25" in response.json()["detail"]
    assert list(jobs_dir.iterdir()) == []


def test_rejects_empty_upload(studio: TestClient, jobs_dir: Path) -> None:
    response = studio.post(
        "/api/upload",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 422
    assert "empty" in response.json()["detail"].lower()
    assert list(jobs_dir.iterdir()) == []


def test_error_is_json_the_page_renders(studio: TestClient) -> None:
    """No traceback text ever reaches the response body."""
    response = studio.post(
        "/api/upload",
        files={"file": ("notes.txt", b"nope", "text/plain")},
    )
    assert "Traceback" not in response.text
    assert response.json()["detail"]
