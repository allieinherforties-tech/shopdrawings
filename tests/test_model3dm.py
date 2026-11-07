"""The .3dm writer: named breps on configured layers with fabrication metadata."""

from pathlib import Path

import rhino3dm

from _builders import layer_names_of, objects_of, read_model, rename_object
from shop_drawings import FurnitureSpec
from shop_drawings.generation.config import GenerationConfig
from shop_drawings.generation.geometry import resolve_part
from shop_drawings.generation.model3dm import build_model, write_model
from shop_drawings.generation.verify import digest_model


def _fixture_model(spec: FurnitureSpec, config: GenerationConfig) -> rhino3dm.File3dm:
    plans = [resolve_part(part) for part in spec.parts]
    return build_model(spec, plans, config)


def test_the_fixture_generates_one_object_per_part_and_feature(
    approved_spec: FurnitureSpec, default_config: GenerationConfig
) -> None:
    model = _fixture_model(approved_spec, default_config)

    names = [obj.Attributes.Name for obj in objects_of(model)]
    assert names[0] == "side_left"
    assert names[1:] == [f"side_left_drill_{index:03d}" for index in range(21)]
    assert len(names) == 22


def test_parts_sit_on_the_part_layer_and_drills_on_the_drill_layer(
    approved_spec: FurnitureSpec, default_config: GenerationConfig
) -> None:
    model = _fixture_model(approved_spec, default_config)

    layer_names = layer_names_of(model)
    objects = objects_of(model)
    assert layer_names == [
        "Default",
        default_config.layers.part,
        default_config.layers.drill,
        default_config.layers.pocket,
    ]
    assert objects[0].Attributes.LayerIndex == layer_names.index(default_config.layers.part)
    assert all(
        obj.Attributes.LayerIndex == layer_names.index(default_config.layers.drill)
        for obj in objects[1:]
    )


def test_fabrication_metadata_is_stamped_as_user_strings(
    approved_spec: FurnitureSpec, default_config: GenerationConfig
) -> None:
    model = _fixture_model(approved_spec, default_config)

    objects = objects_of(model)
    strings = dict(objects[0].Attributes.GetUserStrings())
    assert strings["material"] == "18mm birch plywood"
    assert strings["spec_status"] == "approved"
    assert strings["part_type"] == "panel"
    assert strings["quantity"] == "1"
    assert strings["source"] == "redline-v3.pdf"
    assert strings["edge_banding"] == "front"
    assert strings["dim_length"] == "720"
    assert strings["dim_width"] == "380"
    assert strings["dim_thickness"] == "18"
    drill_strings = dict(objects[1].Attributes.GetUserStrings())
    assert drill_strings["part_id"] == "side_left"
    assert drill_strings["diameter"] == "5"


def test_generated_parts_carry_exact_declared_dimensions(
    approved_spec: FurnitureSpec, default_config: GenerationConfig, tmp_path: Path
) -> None:
    path = write_model(
        approved_spec, [resolve_part(p) for p in approved_spec.parts], default_config, tmp_path
    )

    part_brep = objects_of(read_model(path))[0].Geometry
    bbox = part_brep.GetBoundingBox()
    length = round(bbox.Max.X - bbox.Min.X, 6)
    width = round(bbox.Max.Y - bbox.Min.Y, 6)
    thickness = round(bbox.Max.Z - bbox.Min.Z, 6)
    assert (length, width, thickness) == (720.0, 380.0, 18.0)
    # The part brep is a closed box: eight vertices.
    assert len(part_brep.Vertices) == 8


def test_two_writes_of_the_same_spec_have_identical_content_digests(
    approved_spec: FurnitureSpec, default_config: GenerationConfig, tmp_path: Path
) -> None:
    plans = [resolve_part(part) for part in approved_spec.parts]
    first = write_model(approved_spec, plans, default_config, tmp_path)
    second = write_model(approved_spec, plans, default_config, tmp_path)

    assert digest_model(first) == digest_model(second)


def test_renaming_an_object_changes_its_digest(
    approved_spec: FurnitureSpec, default_config: GenerationConfig, tmp_path: Path
) -> None:
    plans = [resolve_part(part) for part in approved_spec.parts]
    path = write_model(approved_spec, plans, default_config, tmp_path)
    original = digest_model(path)

    rename_object(path, 0, "hand_edited", version=default_config.file3dm_version)

    assert digest_model(path) != original
