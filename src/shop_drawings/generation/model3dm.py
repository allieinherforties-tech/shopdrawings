"""The .3dm writer — deterministic Rhino files from approved specs.

Builds the model with ``rhino3dm`` (no Rhino install or license needed):
one named brep per fabricable part, feature solids (drill cylinders,
pocket boxes) as separate named objects on their machining layers, and
fabrication metadata stamped as user strings. Every convention — layer
names, object naming, archive version — comes from the
:class:`~shop_drawings.generation.config.GenerationConfig`.

Determinism note: rhino3dm assigns each object a random, read-only UUID
and the archive embeds varying metadata, so two writes of the identical
model are never byte-identical. Reproducibility (blueprint §5) is
therefore checked by :func:`~shop_drawings.generation.verify.digest_model`
— a canonical content digest over names, layers, user strings, and
geometry — which catches hand-edited drift just as byte comparison would.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import rhino3dm

from shop_drawings.generation.config import GenerationConfig
from shop_drawings.generation.geometry import Drill, PartPlan, Pocket
from shop_drawings.schema import FurnitureSpec, PartType

#: The layer every .3dm starts with; nothing is generated on it.
DEFAULT_LAYER = "Default"


def _make_layer(model: rhino3dm.File3dm, name: str, color: tuple[int, int, int, int]) -> int:
    index = model.Layers.AddLayer(name, color)
    if index < 0:
        raise ValueError(f"failed to create layer '{name}'")
    return index


def _brep_for_part(plan: PartPlan, origin_x: float) -> rhino3dm.Brep:
    """Part solid as a box brep at its layout position."""
    return rhino3dm.Brep.CreateFromBoundingBox(
        rhino3dm.BoundingBox(
            rhino3dm.Point3d(origin_x, 0, 0),
            rhino3dm.Point3d(origin_x + plan.length, plan.width, plan.thickness),
        )
    )


def _brep_for_drill(drill: Drill, origin_x: float, plan: PartPlan) -> rhino3dm.Brep:
    """A drill as a capped cylinder through the part thickness."""
    circle = rhino3dm.Circle(
        rhino3dm.Point3d(origin_x + drill.center_x, drill.center_y, 0), drill.diameter / 2
    )
    return rhino3dm.Brep.CreateFromCylinder(rhino3dm.Cylinder(circle, plan.thickness), True, True)


def _brep_for_pocket(pocket: Pocket, origin_x: float, plan: PartPlan) -> rhino3dm.Brep:
    """A pocket as a box brep sunk into the +Z face by its cut depth."""
    return rhino3dm.Brep.CreateFromBoundingBox(
        rhino3dm.BoundingBox(
            rhino3dm.Point3d(origin_x + pocket.x, pocket.y, plan.thickness - pocket.depth),
            rhino3dm.Point3d(
                origin_x + pocket.x + pocket.length, pocket.y + pocket.width, plan.thickness
            ),
        )
    )


def _object_for(model: rhino3dm.File3dm, object_id: UUID) -> rhino3dm.File3dmObject:
    """The object just added, by id — for naming and metadata stamping.

    ``FindId`` is present at runtime but missing from rhino3dm 8.35's
    shipped stubs, hence the targeted ignore.
    """
    obj: rhino3dm.File3dmObject = model.Objects.FindId(object_id)  # type: ignore[attr-defined]
    if obj is None:
        raise RuntimeError("rhino3dm did not return the object just added")
    return obj


def _set_identity(attributes: rhino3dm.ObjectAttributes, name: str, layer_index: int) -> None:
    """Set an object's name and layer in one call.

    Both assignments work at runtime (probed against rhino3dm 8.35);
    its shipped stubs omit these setters while declaring others, hence
    the targeted ignores.
    """
    attributes.Name = name  # type: ignore[misc]
    attributes.LayerIndex = layer_index  # type: ignore[misc]


def _stamp_metadata(
    attributes: rhino3dm.ObjectAttributes, spec: FurnitureSpec, plan: PartPlan
) -> None:
    """Fabrication metadata as user strings (blueprint §3)."""
    attributes.SetUserString("material", plan.material)
    attributes.SetUserString("spec_status", "approved")
    attributes.SetUserString("part_type", plan.part_type.value)
    attributes.SetUserString("quantity", str(plan.quantity))
    attributes.SetUserString("source", spec.project.source)
    if plan.edge_banding:
        attributes.SetUserString("edge_banding", " ".join(e.value for e in plan.edge_banding))


def build_model(
    spec: FurnitureSpec, plans: list[PartPlan], config: GenerationConfig
) -> rhino3dm.File3dm:
    """Build the .3dm model object for the approved spec's resolved plans.

    Parts are laid out in a single row along +X with ``config.part_gap``
    spacing so each is individually selectable — the schema carries no
    assembly positions, so v1 generates a part library, not an assembly.
    Hardware plans have no geometry; they appear only in the cut list.
    """
    model = rhino3dm.File3dm()
    _make_layer(model, DEFAULT_LAYER, (255, 255, 255, 255))
    part_layer = _make_layer(model, config.layers.part, (0, 0, 0, 255))
    drill_layer = _make_layer(model, config.layers.drill, config.layers.drill_color)
    pocket_layer = _make_layer(model, config.layers.pocket, config.layers.pocket_color)

    origin_x = 0.0
    for plan in plans:
        if plan.part_type is PartType.HARDWARE:
            continue
        brep = _brep_for_part(plan, origin_x)
        object_id = model.Objects.AddBrep(brep)
        attributes = _object_for(model, object_id).Attributes
        _set_identity(
            attributes, config.naming.part_pattern.format(part_id=plan.part_id), part_layer
        )
        _stamp_metadata(attributes, spec, plan)
        attributes.SetUserString("dim_length", f"{plan.length:g}")
        attributes.SetUserString("dim_width", f"{plan.width:g}")
        attributes.SetUserString("dim_thickness", f"{plan.thickness:g}")

        for index, drill in enumerate(plan.drills):
            drill_id = model.Objects.AddBrep(_brep_for_drill(drill, origin_x, plan))
            drill_attributes = _object_for(model, drill_id).Attributes
            _set_identity(
                drill_attributes,
                config.naming.feature_pattern.format(
                    part_id=plan.part_id, feature_type="drill", index=index
                ),
                drill_layer,
            )
            drill_attributes.SetUserString("part_id", plan.part_id)
            drill_attributes.SetUserString("diameter", f"{drill.diameter:g}")

        for index, pocket in enumerate(plan.pockets):
            pocket_id = model.Objects.AddBrep(_brep_for_pocket(pocket, origin_x, plan))
            pocket_attributes = _object_for(model, pocket_id).Attributes
            _set_identity(
                pocket_attributes,
                config.naming.feature_pattern.format(
                    part_id=plan.part_id, feature_type="pocket", index=index
                ),
                pocket_layer,
            )
            pocket_attributes.SetUserString("part_id", plan.part_id)
            pocket_attributes.SetUserString("cut_depth", f"{pocket.depth:g}")

        origin_x += plan.length + config.part_gap
    return model


def write_model(
    spec: FurnitureSpec,
    plans: list[PartPlan],
    config: GenerationConfig,
    out_dir: Path,
) -> Path:
    """Write the .3dm for the approved spec; returns the file path."""
    model = build_model(spec, plans, config)
    path = out_dir / config.naming.model_file_pattern.format(project=spec.project.name, ext="3dm")
    if not model.Write(str(path), config.file3dm_version):
        raise RuntimeError(f"rhino3dm refused to write {path}")
    return path
