"""Session-scoped full-pipeline runs shared by the E2E and golden-file tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.golden.run_pipeline import PipelineRun, run_full_pipeline

GLOBUS_PDF = Path(__file__).parents[1] / "fixtures" / "globus_redline_v4.pdf"


@pytest.fixture(scope="session")
def pipeline_run(tmp_path_factory: pytest.TempPathFactory) -> PipelineRun:
    """One full pipeline run per test session: extract → review → approve → generate."""
    return run_full_pipeline(tmp_path_factory.mktemp("golden-run"))


@pytest.fixture(scope="session")
def globus_run(tmp_path_factory: pytest.TempPathFactory) -> PipelineRun:
    """The same full pipeline run, on the real two-sheet Globus redline."""
    return run_full_pipeline(
        tmp_path_factory.mktemp("globus-run"), pdf_path=GLOBUS_PDF, project_name=None
    )
