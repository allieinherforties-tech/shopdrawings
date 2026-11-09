"""Session-scoped full-pipeline run shared by the E2E and golden-file tests."""

from __future__ import annotations

import pytest

from tests.golden.run_pipeline import PipelineRun, run_full_pipeline


@pytest.fixture(scope="session")
def pipeline_run(tmp_path_factory: pytest.TempPathFactory) -> PipelineRun:
    """One full pipeline run per test session: extract → review → approve → generate."""
    return run_full_pipeline(tmp_path_factory.mktemp("golden-run"))
