"""Globus golden files and output validation on the real two-sheet redline.

The full pipeline runs on ``globus_redline_v4.pdf`` (text-layer extraction,
no vision client) exactly as it does on the cabinet fixture; the approved
spec, the .3dm content digest, the DXF cut sheet, and the cut list are
pinned under ``files/`` with a ``globus-`` prefix. Beyond the pins, every
output is *re-parsed* — the .3dm read back with rhino3dm and counted
against the spec's 8 part groups, the DXF read back with ezdxf, the cut
list parsed against the spec — so the files are checked as artifacts, not
by trusting that the writer ran.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from ezdxf.entities.circle import Circle
from ezdxf.entities.lwpolyline import LWPolyline
from ezdxf.filemanagement import readfile
from rhino3dm import File3dm

from shop_drawings.generation.config import DXF_INSUNITS, GenerationConfig
from shop_drawings.generation.cutlist import COLUMNS, fmt_dimension
from shop_drawings.generation.errors import UnresolvablePartError
from shop_drawings.generation.geometry import PartPlan, resolve_part
from shop_drawings.generation.verify import digest_model, rhino3dm_read
from shop_drawings.schema import FurnitureSpec, PartType
from tests.generation._builders import layer_names_of, objects_of
from tests.golden.dxf_digest import canonical_dxf_text
from tests.golden.run_pipeline import PipelineRun
from tests.golden.test_golden_files import _pin_or_compare_bytes

#: The Globus redline's eight part groups (the studio's count for this job).
GLOBUS_PART_COUNT = 8


def _plans(spec: FurnitureSpec) -> tuple[list[PartPlan], set[str]]:
    """Resolve every part; returns ``(plans, escalated part ids)``."""
    plans: list[PartPlan] = []
    escalated: set[str] = set()
    for part in spec.parts:
        try:
            plans.append(resolve_part(part))
        except UnresolvablePartError:
            escalated.add(part.id)
    return plans, escalated


def _fabricable(spec: FurnitureSpec) -> list[PartPlan]:
    """The resolved plans that receive geometry (not hardware)."""
    plans, _ = _plans(spec)
    return [plan for plan in plans if plan.part_type is not PartType.HARDWARE]


def objects_on(model: File3dm, layer_name: str) -> list[Any]:
    """The model's objects that sit on ``layer_name``."""
    layer_names = layer_names_of(model)
    return [
        obj for obj in objects_of(model) if layer_names[obj.Attributes.LayerIndex] == layer_name
    ]


def test_globus_extraction_yields_the_eight_part_groups(globus_run: PipelineRun) -> None:
    """The real redline reads into the eight part groups the studio knows."""
    assert len(globus_run.approved_spec.parts) == GLOBUS_PART_COUNT


def test_globus_golden_files_are_reproduced(globus_run: PipelineRun) -> None:
    """Approved spec, model digest, DXF sheet, and cut list all reproduce."""
    generation = globus_run.generation
    assert generation.model_path is not None and generation.cut_list_path is not None
    assert len(generation.sheet_paths) == 1

    _pin_or_compare_bytes("globus-approved-spec.json", globus_run.approved_spec_path.read_bytes())
    _pin_or_compare_bytes(
        "globus-model.content-digest.txt", (digest_model(generation.model_path) + "\n").encode()
    )
    _pin_or_compare_bytes(
        "globus-sheet-01.dxf", canonical_dxf_text(generation.sheet_paths[0]).encode("utf-8")
    )
    _pin_or_compare_bytes("globus-cutlist.csv", generation.cut_list_path.read_bytes())


def test_model_object_counts_match_the_approved_spec(globus_run: PipelineRun) -> None:
    """Re-parse the .3dm: one brep per fabricable part, one object per feature."""
    spec = globus_run.approved_spec
    fabricable = _fabricable(spec)
    _, escalated = _plans(spec)
    # Every part group is accounted for: resolved into geometry or escalated.
    assert len(fabricable) + len(escalated) == GLOBUS_PART_COUNT

    model = rhino3dm_read(globus_run.generation.model_path)
    config = GenerationConfig()
    part_names = {obj.Attributes.Name for obj in objects_on(model, config.layers.part)}
    assert part_names == {plan.part_id for plan in fabricable}

    drill_objects = objects_on(model, config.layers.drill)
    pocket_objects = objects_on(model, config.layers.pocket)
    assert len(drill_objects) == sum(len(plan.drills) for plan in fabricable)
    assert len(pocket_objects) == sum(len(plan.pockets) for plan in fabricable)


def test_dxf_sheets_reparse_with_the_declared_structure(globus_run: PipelineRun) -> None:
    """Re-parse each DXF: units, layers, and one closed outline per part."""
    spec = globus_run.approved_spec
    fabricable = _fabricable(spec)
    config = GenerationConfig()

    for path in globus_run.generation.sheet_paths:
        doc = readfile(str(path))
        assert doc.header.get("$INSUNITS") == DXF_INSUNITS[spec.project.units]
        layer_names = {layer.dxf.name for layer in doc.layers}
        assert {
            config.layers.cut,
            config.layers.pocket,
            config.layers.drill,
            config.layers.sheet,
        } <= layer_names

        msp = doc.modelspace()
        cut_outlines = [
            entity
            for entity in msp
            if isinstance(entity, LWPolyline) and entity.dxf.layer == config.layers.cut
        ]
        assert len(cut_outlines) == len(fabricable)
        assert all(entity.closed for entity in cut_outlines)
        drills = [
            entity
            for entity in msp
            if isinstance(entity, Circle) and entity.dxf.layer == config.layers.drill
        ]
        assert len(drills) == sum(len(plan.drills) for plan in fabricable)


def test_cut_list_rows_match_the_approved_spec(globus_run: PipelineRun) -> None:
    """Re-parse the CSV: every spec part rowed, in order, with its values."""
    spec = globus_run.approved_spec
    plans, escalated = _plans(spec)
    plans_by_id = {plan.part_id: plan for plan in plans}
    assert globus_run.generation.cut_list_path is not None
    text = globus_run.generation.cut_list_path.read_text(encoding="utf-8")

    rows = list(csv.reader(io.StringIO(text, newline="")))
    header, data_rows = rows[0], rows[1:]
    assert tuple(header) == COLUMNS
    assert len(data_rows) == len(spec.parts) == GLOBUS_PART_COUNT

    for row, part in zip(data_rows, spec.parts, strict=True):
        row_by_column = dict(zip(COLUMNS, row, strict=True))
        assert row_by_column["part_id"] == part.id
        assert row_by_column["quantity"] == str(part.quantity)
        if part.id in escalated:
            assert "escalated" in row_by_column["features"]
            continue
        plan = plans_by_id[part.id]
        if plan.part_type is PartType.HARDWARE:
            continue
        assert row_by_column["length"] == fmt_dimension(plan.length), part.id
        assert row_by_column["width"] == fmt_dimension(plan.width), part.id
        assert row_by_column["thickness"] == fmt_dimension(plan.thickness), part.id
