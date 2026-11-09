"""Reconciliation: assemblies become schema-valid drafts with questions."""

from dataclasses import replace

from shop_drawings.extraction import (
    DesignDocument,
    Region,
    TextBlock,
    Unassociated,
    UnassociatedKind,
    assemble,
    build_spec,
)
from shop_drawings.schema import (
    DimensionKind,
    Edge,
    FurnitureSpec,
    ReviewStatus,
    TracedValue,
    Units,
)


def blk(text: str, x: float, y: float, width: float = 0.22) -> TextBlock:
    return TextBlock(page=1, text=text, region=Region(x, y, x + width, y + 0.016))


def document(*blocks: TextBlock) -> DesignDocument:
    return DesignDocument(source_name="test.pdf", pages=(), blocks=blocks)


def full_callout() -> DesignDocument:
    """Label, material, and a three-value size line: no questions expected."""
    return document(
        blk("SIDE PANEL - QTY 2", 0.08, 0.19),
        blk("18MM BIRCH PLYWOOD", 0.08, 0.215),
        blk("720 X 380 X 18", 0.08, 0.24),
        blk("PROJECT: ENTRY-CABINET-001", 0.60, 0.05),
        blk("UNITS: MM", 0.60, 0.08),
        blk("OVERALL WIDTH 800", 0.60, 0.11),
    )


def test_spec_is_schema_valid_and_needs_review() -> None:
    spec = build_spec(assemble(full_callout()), source_name="test.pdf")
    FurnitureSpec.model_validate(spec.model_dump())  # raises if invalid
    assert spec.status is ReviewStatus.NEEDS_REVIEW
    assert all(part.status is ReviewStatus.NEEDS_REVIEW for part in spec.parts)


def test_sizes_become_typed_dimensions() -> None:
    spec = build_spec(assemble(full_callout()), source_name="test.pdf")
    part = spec.parts[0]
    assert part.id == "side_panel"
    assert part.quantity == 2
    assert part.material == "birch plywood"
    assert part.dimensions[DimensionKind.LENGTH].value == 720.0
    assert part.dimensions[DimensionKind.WIDTH].value == 380.0
    assert part.dimensions[DimensionKind.THICKNESS].value == 18.0


def test_every_dimension_carries_source_and_confidence() -> None:
    spec = build_spec(assemble(full_callout()), source_name="test.pdf")
    values: list[TracedValue] = [d for part in spec.parts for d in part.dimensions.values()]
    values += [d for part in spec.parts for f in part.features for d in f.dimensions.values()]
    values += [c for c in spec.constraints]
    assert values  # the fixture has values to check
    for value in values:
        assert value.source.page >= 1
        assert value.source.text.strip()
        x0, y0, x1, y1 = value.source.region
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
        assert 0.0 <= value.confidence <= 1.0


def test_title_block_drives_project_and_units() -> None:
    spec = build_spec(assemble(full_callout()), source_name="test.pdf")
    assert spec.project.name == "ENTRY-CABINET-001"
    assert spec.project.units is Units.MM
    assert spec.project.source == "test.pdf"


def test_missing_units_defaults_to_mm_with_a_question() -> None:
    doc = document(
        blk("SIDE PANEL - QTY 2", 0.08, 0.19),
        blk("18MM BIRCH PLYWOOD", 0.08, 0.215),
        blk("720 X 380 X 18", 0.08, 0.24),
    )
    spec = build_spec(assemble(doc), source_name="test.pdf")
    assert spec.project.units is Units.MM
    assert any("units" in q.lower() for q in spec.unresolved_questions)


def test_two_value_size_line_keeps_its_interpretation_question() -> None:
    doc = document(
        blk("SHELF - QTY 3", 0.08, 0.19),
        blk("18MM BIRCH PLYWOOD", 0.08, 0.215),
        blk("764 X 340", 0.08, 0.24),
    )
    spec = build_spec(assemble(doc), source_name="test.pdf")
    part = spec.parts[0]
    assert part.dimensions[DimensionKind.LENGTH].value == 764.0
    assert part.dimensions[DimensionKind.WIDTH].value == 340.0
    assert any("length × width" in q for q in spec.unresolved_questions)


def test_missing_material_is_recorded_not_guessed() -> None:
    doc = document(
        blk("DRAWER FRONT - QTY 1", 0.08, 0.19),
        blk("720 X 380 X 18", 0.08, 0.24),
    )
    spec = build_spec(assemble(doc), source_name="test.pdf")
    part = spec.parts[0]
    assert part.material == "unspecified"
    assert any("no material" in q for q in spec.unresolved_questions)


def test_edge_banding_maps_to_schema_edges() -> None:
    doc = document(
        blk("TOP - QTY 1", 0.08, 0.19),
        blk("800 X 380 X 18", 0.08, 0.215),
        blk("EDGE BANDING: FRONT, TOP", 0.08, 0.24),
    )
    spec = build_spec(assemble(doc), source_name="test.pdf")
    assert spec.parts[0].edge_banding == [Edge.FRONT, Edge.TOP]


def test_hole_callout_becomes_a_feature() -> None:
    doc = document(
        blk("SIDE PANEL - QTY 2", 0.08, 0.19),
        blk("18MM BIRCH PLYWOOD", 0.08, 0.215),
        blk("720 X 380 X 18", 0.08, 0.24),
        blk("SHELF PIN HOLES Ø5 @ 32, 37 FROM FRONT EDGE", 0.08, 0.265),
    )
    spec = build_spec(assemble(doc), source_name="test.pdf")
    feature = spec.parts[0].features[0]
    assert feature.type == "shelf_pin_holes"
    assert feature.dimensions[DimensionKind.HOLE_DIAMETER].value == 5.0
    assert feature.dimensions[DimensionKind.SPACING].value == 32.0
    setback = feature.dimensions[DimensionKind.SETBACK]
    assert setback.value == 37.0
    assert setback.reference == "front_edge"


def test_joinery_note_feature_carries_its_note() -> None:
    doc = document(
        blk("SHELF - QTY 3", 0.08, 0.19),
        blk("18MM BIRCH PLYWOOD", 0.08, 0.215),
        blk("764 X 340 X 18", 0.08, 0.24),
        blk("DADO 6mm FOR BACK PANEL", 0.08, 0.265),
    )
    spec = build_spec(assemble(doc), source_name="test.pdf")
    feature = spec.parts[0].features[0]
    assert feature.type == "dado"
    assert feature.note == "DADO 6mm FOR BACK PANEL"
    # The feature's trace lives in the part's context references.
    texts = [ref.text for ref in spec.parts[0].source_references]
    assert "DADO 6mm FOR BACK PANEL" in texts


def test_constraints_carry_type_and_source() -> None:
    spec = build_spec(assemble(full_callout()), source_name="test.pdf")
    constraint = spec.constraints[0]
    assert constraint.type == "overall_width"
    assert constraint.value == 800.0
    assert constraint.source.text == "OVERALL WIDTH 800"


def test_conflicting_constraints_surface_a_question() -> None:
    doc = document(
        blk("OVERALL WIDTH 800", 0.60, 0.11),
        blk("OVERALL WIDTH 900", 0.60, 0.14),
    )
    spec = build_spec(assemble(doc), source_name="test.pdf")
    assert len(spec.constraints) == 1  # first claim wins in the draft
    assert any("800" in q and "900" in q for q in spec.unresolved_questions)


def test_unassociated_findings_become_questions() -> None:
    assembly = assemble(
        document(
            blk("SIDE PANEL - QTY 2", 0.08, 0.19),
            blk("18MM BIRCH PLYWOOD", 0.08, 0.215),
            blk("720 X 380 X 18", 0.08, 0.24),
            blk("500", 0.60, 0.30),
        )
    )
    assert assembly.unassociated[0].kind is UnassociatedKind.BARE_NUMBER
    spec = build_spec(assembly, source_name="test.pdf")
    assert any("500" in q for q in spec.unresolved_questions)


def test_rejected_vision_readings_land_in_questions() -> None:
    assembly = assemble(full_callout())
    extra = Unassociated(
        kind=UnassociatedKind.LOW_CONFIDENCE_DIMENSION,
        detail="vision reading '390' (role depth) reports confidence 0.40",
        block=blk("390", 0.70, 0.50),
    )
    patched = replace(assembly, unassociated=(*assembly.unassociated, extra))
    spec = build_spec(patched, source_name="test.pdf")
    assert any("vision reading" in q for q in spec.unresolved_questions)
