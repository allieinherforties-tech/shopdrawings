"""Shared spec builders for tests: approved parts with arbitrary shapes."""

from pathlib import Path
from typing import Any

import rhino3dm

from shop_drawings import FurnitureSpec
from shop_drawings.schema import Part


def read_model(path: Path) -> rhino3dm.File3dm:
    """Read a written .3dm back in."""
    return rhino3dm.File3dm.Read(str(path))


def objects_of(model: rhino3dm.File3dm) -> list[Any]:
    """The model's objects as a plain list.

    The rhino3dm 8.35 stubs omit ObjectTable indexing and ``list()``
    accepts it only as an untyped iterable; every writer test needs both,
    so the workaround lives here once instead of a dozen ignores across
    the suite.
    """
    objects: list[Any] = list(model.Objects)  # type: ignore[call-overload]
    return objects


def layer_names_of(model: rhino3dm.File3dm) -> list[str]:
    """The model's layer table, in table order."""
    return [layer.Name for layer in model.Layers]


def rename_object(path: Path, index: int, name: str, version: int = 8) -> None:
    """Rename one object in a written .3dm, in place — simulating a hand edit."""
    model = read_model(path)
    model.Objects[index].Attributes.Name = name  # type: ignore[index]
    assert model.Write(str(path), version)


def spec_with_parts(
    approved_spec_dict: dict[str, Any], part_dicts: list[dict[str, Any]]
) -> FurnitureSpec:
    """Append parts in place (approving their statuses) and validate the spec."""
    for part_dict in part_dicts:
        part_dict.setdefault("status", "approved")
        for dimension in part_dict.get("dimensions", {}).values():
            dimension.setdefault("status", "approved")
        for feature in part_dict.get("features", []):
            for dimension in feature.get("dimensions", {}).values():
                dimension.setdefault("status", "approved")
        approved_spec_dict["parts"].append(part_dict)
    return FurnitureSpec.model_validate(approved_spec_dict)


def approved_part(approved_spec_dict: dict[str, Any], part_dict: dict[str, Any]) -> Part:
    """A validated approved spec carrying one extra part; returns that part."""
    return spec_with_parts(approved_spec_dict, [part_dict]).parts[-1]


def approved_feature(kind: str, dimensions: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """A feature dict with every dimension approved."""
    approved = {name: {**dim, "status": "approved"} for name, dim in dimensions.items()}
    return {"type": kind, "dimensions": approved, **extra}


def sourced_dimension(value: float, text: str | None = None, **extra: Any) -> dict[str, Any]:
    """A dimension value carrying a plausible source crop."""
    return {
        "value": value,
        "source": {
            "page": 2,
            "region": [0.05, 0.05, 0.2, 0.2],
            "text": text or str(int(value)),
        },
        "confidence": 0.9,
        **extra,
    }
