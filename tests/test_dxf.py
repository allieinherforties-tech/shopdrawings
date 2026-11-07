"""The DXF cut sheets: closed contours, fabrication layers, sheet discipline."""

from pathlib import Path
from typing import Any

import pytest
from ezdxf.entities.circle import Circle
from ezdxf.entities.lwpolyline import LWPolyline
from ezdxf.filemanagement import readfile

from shop_drawings import FurnitureSpec, PartType
from shop_drawings.generation.config import DXF_INSUNITS, GenerationConfig
from shop_drawings.generation.dxf import layout_plans, write_cut_sheets
from shop_drawings.generation.errors import FabricationError
from shop_drawings.generation.geometry import Drill, PartPlan, resolve_part


@pytest.fixture
def sheet_paths(
    approved_spec: FurnitureSpec, default_config: GenerationConfig, tmp_path: Path
) -> list[Path]:
    """One written cut sheet for the fixture cabinet."""
    plans = [resolve_part(part) for part in approved_spec.parts]
    return write_cut_sheets(
        plans,
        default_config,
        DXF_INSUNITS[approved_spec.project.units],
        tmp_path,
        approved_spec.project.name,
    )


def test_the_fixture_fits_one_sheet(
    approved_spec: FurnitureSpec, default_config: GenerationConfig
) -> None:
    plans = [resolve_part(part) for part in approved_spec.parts]

    sheets = layout_plans(plans, default_config)

    assert len(sheets) == 1
    assert len(sheets[0].placed) == 1


def test_written_sheets_declare_millimeter_units(
    sheet_paths: list[Path],
) -> None:
    assert len(sheet_paths) == 1
    doc = readfile(str(sheet_paths[0]))

    assert doc.header.get("$INSUNITS") == 4  # millimeters
    assert doc.units == 4


def test_written_sheets_declare_the_configured_layers(
    sheet_paths: list[Path], default_config: GenerationConfig
) -> None:
    doc = readfile(str(sheet_paths[0]))

    names = {layer.dxf.name for layer in doc.layers}
    assert {
        default_config.layers.cut,
        default_config.layers.pocket,
        default_config.layers.drill,
        default_config.layers.sheet,
    } <= names


def test_the_part_outline_is_one_closed_contour_on_the_cut_layer(
    sheet_paths: list[Path], default_config: GenerationConfig
) -> None:
    doc = readfile(str(sheet_paths[0]))

    cut_polylines = [
        entity
        for entity in doc.modelspace()
        if isinstance(entity, LWPolyline) and entity.dxf.layer == default_config.layers.cut
    ]
    assert len(cut_polylines) == 1
    outline = cut_polylines[0]
    assert outline.closed
    points = list(outline.get_points("xy"))
    assert len(points) == 4
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    assert round(max(xs) - min(xs), 6) == 720.0
    assert round(max(ys) - min(ys), 6) == 380.0


def test_drill_holes_are_true_circles_with_the_specified_radius(
    sheet_paths: list[Path], default_config: GenerationConfig
) -> None:
    doc = readfile(str(sheet_paths[0]))

    circles = [
        entity
        for entity in doc.modelspace()
        if isinstance(entity, Circle) and entity.dxf.layer == default_config.layers.drill
    ]
    assert len(circles) == 21
    assert all(round(circle.dxf.radius, 6) == 2.5 for circle in circles)
    centers = sorted(round(circle.dxf.center.x, 6) for circle in circles)
    assert centers[0] > 0  # placed inside the part outline, not at the origin


def test_the_sheet_boundary_is_drawn_and_parts_stay_inside_the_margin(
    sheet_paths: list[Path], default_config: GenerationConfig
) -> None:
    doc = readfile(str(sheet_paths[0]))
    msp = doc.modelspace()

    sheet_frames = [
        entity
        for entity in msp
        if isinstance(entity, LWPolyline) and entity.dxf.layer == default_config.layers.sheet
    ]
    assert len(sheet_frames) == 1
    frame_points = list(sheet_frames[0].get_points("xy"))
    xs, ys = [p[0] for p in frame_points], [p[1] for p in frame_points]
    assert round(max(xs) - min(xs), 6) == 2440.0
    assert round(max(ys) - min(ys), 6) == 1220.0


def test_a_part_that_fits_no_stocked_sheet_is_refused(
    approved_spec_dict: dict[str, Any], default_config: GenerationConfig
) -> None:
    part = approved_spec_dict["parts"][0]
    part["dimensions"]["length"]["value"] = 3000
    part["dimensions"]["width"]["value"] = 2500
    plans = [resolve_part(p) for p in FurnitureSpec.model_validate(approved_spec_dict).parts]

    with pytest.raises(FabricationError, match="fits no configured sheet stock"):
        layout_plans(plans, default_config)


def test_sheet_layout_never_overlaps_parts(default_config: GenerationConfig) -> None:
    plans = [
        PartPlan(
            part_id=f"panel_{index}",
            part_type=PartType.PANEL,
            material="18mm birch plywood",
            quantity=1,
            length=600.0,
            width=400.0,
            thickness=18.0,
            edge_banding=(),
            drills=(Drill(center_x=100.0, center_y=100.0, diameter=5.0),),
            pockets=(),
        )
        for index in range(8)
    ]

    sheets = layout_plans(plans, default_config)

    for sheet in sheets:
        for i, a in enumerate(sheet.placed):
            for b in sheet.placed[i + 1 :]:
                separated = (
                    a.x + a.plan.length + default_config.part_gap <= b.x
                    or b.x + b.plan.length + default_config.part_gap <= a.x
                    or a.y + a.plan.width + default_config.part_gap <= b.y
                    or b.y + b.plan.width + default_config.part_gap <= a.y
                )
                assert separated, f"{a.plan.part_id} and {b.plan.part_id} overlap"
    total = sum(len(sheet.placed) for sheet in sheets)
    assert total == 8


def test_sheet_file_names_follow_the_configured_pattern(
    sheet_paths: list[Path],
) -> None:
    assert sheet_paths[0].name == "entry-cabinet-001_sheet-01.dxf"
