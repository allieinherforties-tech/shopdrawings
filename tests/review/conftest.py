"""Shared fixtures for review-gate tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
EXAMPLE = REPO_ROOT / "examples" / "entry-cabinet-review.json"


@pytest.fixture
def spec_file(tmp_path: Path) -> Path:
    """A writable copy of the demo fixture spec."""
    dest = tmp_path / "entry-cabinet-review.json"
    shutil.copy(EXAMPLE, dest)
    return dest
