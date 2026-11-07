"""Shared fixtures: the blueprint §3 cabinet example, as raw spec dicts."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def cabinet_spec_dict() -> dict[str, Any]:
    """The blueprint's entry-cabinet example, in draft (needs_review) state."""
    raw = (FIXTURES_DIR / "entry_cabinet.json").read_text(encoding="utf-8")
    spec: dict[str, Any] = json.loads(raw)
    return spec


@pytest.fixture
def approved_spec_dict(cabinet_spec_dict: dict[str, Any]) -> dict[str, Any]:
    """The same cabinet with every status approved and no open questions."""
    spec = copy.deepcopy(cabinet_spec_dict)
    spec["status"] = "approved"
    spec["unresolved_questions"] = []
    for part in spec["parts"]:
        part["status"] = "approved"
        for dimension in part["dimensions"].values():
            dimension["status"] = "approved"
        for feature in part["features"]:
            for dimension in feature["dimensions"].values():
                dimension["status"] = "approved"
    for constraint in spec["constraints"]:
        constraint["status"] = "approved"
    return spec
