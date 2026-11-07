"""Automatic verification: the blueprint §5 checks that run on every job."""

from pathlib import Path
from typing import Any

import ezdxf
import pytest
from ezdxf.entities.lwpolyline import LWPolyline
from ezdxf.filemanagement import readfile

from _builders import rename_object
from shop_drawings import FurnitureSpec, PartType, Units
from shop_drawings.generation.config import GenerationConfig
from shop_drawings.generation.dxf import write_cut_sheets
from shop_drawings.generation.geometry import Drill, PartPlan, Pocket, resolve_part
from shop_drawings.generation.model3dm import write_model
from shop_drawings.generation.verify import (
    CheckStatus,
    check_constraint_closure,
    check_dxf_sheets,
    check_geometric_sanity,
    check_roundtrip,
    check_traceability,
)


def _panel_plan(**overrides: Any) -> PartPlan:
    """A resolvable 720x380x18 panel carrying one safe drill."""
    fields: dict[str, Any] = {
        "part_id": "side_left",
        "part_type": PartType.PANEL,
        "material": "18mm birch plywood",
        "quantity": 1,
        "length": 720.0,
        "width": 380.0,
        "thickness": 18.0,
        "edge_banding": (),
        "drills": (Drill(center_x=100.0, center_y=100.0, diameter=5.0),),
        "pockets": (),
    }
    fields.update(overrides)
    return PartPlan(**fields)


def test_traceability_passes_on_the_approved_fixture(approved_spec: FurnitureSpec) -> None:
    result = check_traceability(approved_spec)

    assert result.status is CheckStatus.PASS
    assert result.failures == ()


def test_geometric_sanity_passes_on_fixture_geometry(approved_spec: FurnitureSpec) -> None:
    plans = [resolve_part(part) for part in approved_spec.parts]

    result = check_geometric_sanity(approved_spec, plans)

    assert result.status is CheckStatus.PASS
    assert result.failures == ()


def test_a_drill_reaching_past_the_panel_edge_fails(approved_spec: FurnitureSpec) -> None:
    plan = _panel_plan(
        drills=(Drill(center_x=719.0, center_y=100.0, diameter=5.0),),
    )

    result = check_geometric_sanity(approved_spec, [plan])

    assert result.status is CheckStatus.FAIL
    assert any("run-axis edge" in failure for failure in result.failures)


def test_overlapping_drills_on_one_column_fail(approved_spec: FurnitureSpec) -> None:
    plan = _panel_plan(
        drills=(
            Drill(center_x=100.0, center_y=100.0, diameter=5.0),
            Drill(center_x=103.0, center_y=100.0, diameter=5.0),
        ),
    )

    result = check_geometric_sanity(approved_spec, [plan])

    assert result.status is CheckStatus.FAIL
    assert any("drills overlap" in failure for failure in result.failures)


def test_a_pocket_past_the_panel_edge_fails(approved_spec: FurnitureSpec) -> None:
    plan = _panel_plan(
        drills=(),
        pockets=(Pocket(x=710.0, y=100.0, length=20.0, width=20.0, depth=6.0),),
    )

    result = check_geometric_sanity(approved_spec, [plan])

    assert result.status is CheckStatus.FAIL
    assert any("extends past the panel edge" in failure for failure in result.failures)


def test_a_pocket_deeper_than_the_stock_fails(approved_spec: FurnitureSpec) -> None:
    plan = _panel_plan(
        drills=(),
        pockets=(Pocket(x=100.0, y=100.0, length=20.0, width=20.0, depth=20.0),),
    )

    result = check_geometric_sanity(approved_spec, [plan])

    assert result.status is CheckStatus.FAIL
    assert any("exceeds thickness" in failure for failure in result.failures)


def test_thickness_disagreeing_with_declared_stock_fails(
    approved_spec: FurnitureSpec,
) -> None:
    plan = _panel_plan(thickness=19.0, drills=())

    result = check_geometric_sanity(approved_spec, [plan])

    assert result.status is CheckStatus.FAIL
    assert any("does not match declared stock" in failure for failure in result.failures)


def test_material_without_a_stated_thickness_records_a_note_not_a_failure(
    approved_spec_dict: dict[str, Any],
) -> None:
    approved_spec_dict["parts"][0]["material"] = "walnut"
    spec = FurnitureSpec.model_validate(approved_spec_dict)
    plan = _panel_plan(drills=())

    result = check_geometric_sanity(spec, [plan])

    assert result.status is CheckStatus.PASS
    assert any("states no mm thickness" in note for note in result.notes)


def test_constraint_closure_is_loudly_skipped(approved_spec: FurnitureSpec) -> None:
    result = check_constraint_closure(approved_spec)

    assert result.status is CheckStatus.SKIPPED
    assert any("composition" in note for note in result.notes)


def test_roundtrip_passes_on_a_just_written_model(
    approved_spec: FurnitureSpec, default_config: GenerationConfig, tmp_path: Path
) -> None:
    plans = [resolve_part(part) for part in approved_spec.parts]
    model_path = write_model(approved_spec, plans, default_config, tmp_path)

    result = check_roundtrip(approved_spec, plans, default_config, model_path)

    assert result.status is CheckStatus.PASS


def test_roundtrip_fails_after_a_hand_edit(
    approved_spec: FurnitureSpec, default_config: GenerationConfig, tmp_path: Path
) -> None:
    plans = [resolve_part(part) for part in approved_spec.parts]
    model_path = write_model(approved_spec, plans, default_config, tmp_path)
    rename_object(model_path, 0, "hand_edited", version=default_config.file3dm_version)

    result = check_roundtrip(approved_spec, plans, default_config, model_path)

    assert result.status is CheckStatus.FAIL
    assert any("modified after generation" in failure for failure in result.failures)


@pytest.fixture
def fixture_sheets(
    approved_spec: FurnitureSpec, default_config: GenerationConfig, tmp_path: Path
) -> list[Path]:
    """Written cut sheets for the approved cabinet fixture."""
    plans = [resolve_part(part) for part in approved_spec.parts]
    return write_cut_sheets(plans, default_config, 4, tmp_path, approved_spec.project.name)


def test_dxf_checks_pass_on_fixture_sheets(
    fixture_sheets: list[Path], default_config: GenerationConfig
) -> None:
    result = check_dxf_sheets(fixture_sheets, default_config, Units.MM)

    assert result.status is CheckStatus.PASS
    assert result.failures == ()


def _rewritten(path: Path, mutate: Any) -> Path:
    """Reopen a DXF, apply a mutation to its modelspace/header, save."""
    doc = readfile(str(path))
    mutate(doc)
    doc.saveas(path)
    return path


def test_an_open_cut_contour_fails_the_fabrication_checks(
    fixture_sheets: list[Path], default_config: GenerationConfig
) -> None:
    def open_contour(doc: ezdxf.document.Drawing) -> None:
        doc.modelspace().add_lwpolyline(
            [(100, 100), (200, 200)],
            format="xy",
            close=False,
            dxfattribs={"layer": default_config.layers.cut},
        )

    path = _rewritten(fixture_sheets[0], open_contour)

    result = check_dxf_sheets([path], default_config, Units.MM)
    assert result.status is CheckStatus.FAIL
    assert any("not closed" in failure for failure in result.failures)
    assert any("2 points" in failure for failure in result.failures)


def test_a_duplicated_cut_contour_fails_the_fabrication_checks(
    fixture_sheets: list[Path], default_config: GenerationConfig
) -> None:
    def duplicate(doc: ezdxf.document.Drawing) -> None:
        msp = doc.modelspace()
        original = next(
            entity
            for entity in msp
            if isinstance(entity, LWPolyline) and entity.dxf.layer == default_config.layers.cut
        )
        points = list(original.get_points("xy"))
        msp.add_lwpolyline(
            points, format="xy", close=True, dxfattribs={"layer": default_config.layers.cut}
        )

    path = _rewritten(fixture_sheets[0], duplicate)

    result = check_dxf_sheets([path], default_config, Units.MM)
    assert result.status is CheckStatus.FAIL
    assert any("duplicate" in failure for failure in result.failures)


def test_wrong_sheet_units_fail_the_fabrication_checks(
    fixture_sheets: list[Path], default_config: GenerationConfig
) -> None:
    def inches(doc: ezdxf.document.Drawing) -> None:
        doc.header["$INSUNITS"] = 1

    path = _rewritten(fixture_sheets[0], inches)

    result = check_dxf_sheets([path], default_config, Units.MM)
    assert result.status is CheckStatus.FAIL
    assert any("$INSUNITS" in failure for failure in result.failures)


def test_an_outline_off_the_usable_sheet_area_fails(
    fixture_sheets: list[Path], default_config: GenerationConfig
) -> None:
    def off_sheet(doc: ezdxf.document.Drawing) -> None:
        doc.modelspace().add_lwpolyline(
            [(3000, 100), (3200, 100), (3200, 300), (3000, 300)],
            format="xy",
            close=True,
            dxfattribs={"layer": default_config.layers.cut},
        )

    path = _rewritten(fixture_sheets[0], off_sheet)

    result = check_dxf_sheets([path], default_config, Units.MM)
    assert result.status is CheckStatus.FAIL
    assert any("leaves the usable sheet area" in failure for failure in result.failures)
