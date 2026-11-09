"""Shared fixtures: the blueprint §3 cabinet example, as raw spec dicts.

Integrated suite: the raw-dict builders (validation and review tests) and the
typed fixtures (generation tests) share one conftest, so every layer's tests
build the same cabinet the same way.
"""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from shop_drawings import FurnitureSpec
from shop_drawings.generation.config import GenerationConfig

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


@pytest.fixture
def approved_spec(approved_spec_dict: dict[str, Any]) -> FurnitureSpec:
    """The approved cabinet fixture, validated against the real schema."""
    return FurnitureSpec.model_validate(approved_spec_dict)


@pytest.fixture
def default_config() -> GenerationConfig:
    """The v1 default studio configuration."""
    return GenerationConfig()


def _source(text: str, page: int = 1) -> dict[str, Any]:
    return {"page": page, "region": [0.1, 0.1, 0.3, 0.2], "text": text}


def _dimension(value: float, text: str | None = None, tolerance: float = 0.0) -> dict[str, Any]:
    return {
        "value": value,
        "tolerance": tolerance,
        "source": _source(text if text is not None else f"{value:g}"),
        "confidence": 0.99,
        "status": "approved",
    }


def _panel(
    part_id: str,
    quantity: int = 1,
    length: float | None = 720,
    width: float | None = 380,
    thickness: float | None = 18,
    features: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A panel part with the given extents; ``None`` omits a dimension."""
    dimensions: dict[str, Any] = {}
    for kind, value in (("length", length), ("width", width), ("thickness", thickness)):
        if value is not None:
            dimensions[kind] = _dimension(value)
    return {
        "id": part_id,
        "type": "panel",
        "quantity": quantity,
        "material": "18mm birch plywood",
        "dimensions": dimensions,
        "edge_banding": [],
        "features": features or [],
        "source_references": [_source(f"{part_id} elevation")],
        "status": "approved",
    }


def _shelf_pins(diameter: float = 5, spacing: float = 32, setback: float = 37) -> dict[str, Any]:
    dimensions: dict[str, Any] = {
        "hole_diameter": _dimension(diameter),
        "spacing": _dimension(spacing),
    }
    if setback is not None:
        dimensions["setback"] = {**_dimension(setback), "reference": "front_edge"}
    return {"type": "shelf_pin_holes", "dimensions": dimensions}


def _cabinet_spec() -> dict[str, Any]:
    """A fully approved cabinet that passes every structural check.

    Two 18 mm side panels plus a 764 mm bottom close to the 800 mm overall
    width (18 + 18 + 764 = 800), the shelf-pin pattern fits the faces, and
    every value carries a source reference.
    """
    return {
        "schema_version": 1,
        "project": {"name": "test-cabinet-001", "units": "mm", "source": "design.pdf"},
        "parts": [
            _panel("side_left", quantity=2, features=[_shelf_pins()]),
            _panel("bottom", length=720, width=764, thickness=18),
        ],
        "constraints": [
            {
                "type": "overall_width",
                "value": 800,
                "tolerance": 1,
                "source": _source("800"),
                "confidence": 0.99,
                "status": "approved",
            }
        ],
        "unresolved_questions": [],
        "status": "approved",
    }


@pytest.fixture
def cabinet() -> dict[str, Any]:
    """An approved, structurally valid cabinet spec dict, fresh per test."""
    return _cabinet_spec()


@pytest.fixture
def make_spec() -> Callable[..., dict[str, Any]]:
    """Build a custom spec: ``make_spec(parts=[...], constraints=[...])``."""

    def factory(
        parts: list[dict[str, Any]] | None = None,
        constraints: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        spec = _cabinet_spec()
        if parts is not None:
            spec["parts"] = parts
        if constraints is not None:
            spec["constraints"] = constraints
        spec.update(overrides)
        return spec

    return factory


@pytest.fixture
def constraint() -> Callable[..., dict[str, Any]]:
    """A single approved constraint dict, e.g. ``constraint("overall_width", 800)``."""

    def factory(
        constraint_type: str = "overall_width",
        value: float = 800,
        tolerance: float = 1,
        text: str | None = None,
    ) -> dict[str, Any]:
        return {
            "type": constraint_type,
            "value": value,
            "tolerance": tolerance,
            "source": _source(text if text is not None else f"{value:g}"),
            "confidence": 0.99,
            "status": "approved",
        }

    return factory
