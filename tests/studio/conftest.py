"""Shared fixtures for studio front-door tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shop_drawings.studio.app import create_studio_app
from tests.fixtures.cabinet_pdf import create_cabinet_pdf


@pytest.fixture
def jobs_dir(tmp_path: Path) -> Path:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    return jobs


@pytest.fixture
def studio(jobs_dir: Path) -> TestClient:
    """The studio front door; redirects left unfollowed for asserting."""
    return TestClient(create_studio_app(jobs_dir), follow_redirects=False)


@pytest.fixture
def cabinet_pdf(tmp_path: Path) -> Path:
    return create_cabinet_pdf(tmp_path / "entry-cabinet.pdf")


@pytest.fixture
def uploaded_job_id(studio: TestClient, cabinet_pdf: Path) -> str:
    """A completed upload's job id, from the redirect's location."""
    response = studio.post(
        "/api/upload",
        files={"file": ("entry-cabinet.pdf", cabinet_pdf.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 303, response.text
    return response.headers["location"].split("/")[2]
