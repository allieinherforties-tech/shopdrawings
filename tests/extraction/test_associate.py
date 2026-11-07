"""Association: callout columns attach findings; everything else asks."""

import pytest

from shop_drawings.extraction import (
    DesignDocument,
    Region,
    TextBlock,
    UnassociatedKind,
    assemble,
)


def blk(text: str, x: float, y: float, width: float = 0.22) -> TextBlock:
    """A text block at a hand-placed position (y measured from the page top)."""
    return TextBlock(page=1, text=text, region=Region(x, y, x + width, y + 0.016))


def column(*items: tuple[str, float]) -> DesignDocument:
    """A one-column document: (text, y) pairs down the left edge."""
    return DesignDocument(
        source_name="test.pdf",
        pages=(),
        blocks=tuple(blk(text, 0.08, y) for text, y in items),
    )


def test_callout_column_adopts_its_members() -> None:
    assembly = assemble(
        column(
            ("SIDE PANEL - QTY 2", 0.19),
            ("18MM BIRCH PLYWOOD", 0.215),
            ("720 X 380", 0.24),
            ("DEPTH 380", 0.265),
        )
    )
    assert not assembly.unassociated
    assert len(assembly.parts) == 1
    part = assembly.parts[0]
    assert part.label == "SIDE PANEL"
    assert part.quantity == 2
    assert part.material == "birch plywood"
    assert part.material_thickness == 18.0
    assert part.sizes == (720.0, 380.0)
    attached = part.dimensions[0]
    assert attached.dimension.kind == "depth"
    assert attached.association == pytest.approx(1.0)


def test_in_column_dimensions_carry_full_confidence() -> None:
    assembly = assemble(
        column(
            ("SIDE PANEL - QTY 2", 0.19),
            ("18MM BIRCH PLYWOOD", 0.215),
            ("DEPTH 380", 0.24),
        )
    )
    attached = assembly.parts[0].dimensions[0]
    assert attached.confidence == pytest.approx(attached.dimension.confidence)


def test_floating_dimension_attaches_with_distance_decay() -> None:
    # A tagged dimension just outside the column (x offset > tolerance) but
    # essentially touching the callout's bounding region: it attaches, but
    # at decayed confidence — never a same-column line's 1.0.
    floating = assemble(
        DesignDocument(
            source_name="test.pdf",
            pages=(),
            blocks=(
                blk("SIDE PANEL - QTY 2", 0.08, 0.19),
                blk("18MM BIRCH PLYWOOD", 0.08, 0.215),
                blk("WIDTH 400", 0.13, 0.20),
            ),
        )
    )
    assert not floating.unassociated
    attached = floating.parts[0].dimensions[0]
    assert attached.dimension.kind == "width"
    assert 0.0 < attached.association < 1.0


def test_far_floating_dimension_becomes_a_question() -> None:
    assembly = assemble(
        column(
            ("SIDE PANEL - QTY 2", 0.19),
            ("18MM BIRCH PLYWOOD", 0.215),
            ("WIDTH 400", 0.60),  # far from the callout
        )
    )
    assert not assembly.parts[0].dimensions
    assert assembly.unassociated[0].kind is UnassociatedKind.LOW_CONFIDENCE_DIMENSION


def test_equidistant_dimension_is_ambiguous() -> None:
    assembly = assemble(
        DesignDocument(
            source_name="test.pdf",
            pages=(),
            blocks=(
                blk("SIDE PANEL - QTY 2", 0.08, 0.19),
                blk("18MM BIRCH PLYWOOD", 0.08, 0.215),
                blk("SHELF - QTY 3", 0.08, 0.60),
                blk("18MM BIRCH PLYWOOD", 0.08, 0.625),
                # Centered between the two callouts.
                blk("WIDTH 400", 0.35, 0.395),
            ),
        )
    )
    for part in assembly.parts:
        assert part.dimensions == ()
    assert assembly.unassociated[0].kind is UnassociatedKind.AMBIGUOUS_ASSOCIATION


def test_bare_number_is_a_question_even_in_column() -> None:
    assembly = assemble(
        column(
            ("SIDE PANEL - QTY 2", 0.19),
            ("18MM BIRCH PLYWOOD", 0.215),
            ("500", 0.24),
        )
    )
    assert assembly.parts[0].sizes == ()
    assert assembly.unassociated[0].kind is UnassociatedKind.BARE_NUMBER


def test_orphaned_material_line_becomes_a_question() -> None:
    assembly = assemble(column(("18MM BIRCH PLYWOOD", 0.19)))
    assert not assembly.parts
    assert assembly.unassociated[0].kind is UnassociatedKind.UNPARSEABLE_ANNOTATION


def test_narrative_prose_produces_no_questions() -> None:
    assembly = assemble(column(("Shop Drawings — Entry Cabinet", 0.19)))
    assert not assembly.parts
    assert not assembly.unassociated


def test_title_and_constraints_are_routed() -> None:
    assembly = assemble(
        DesignDocument(
            source_name="test.pdf",
            pages=(),
            blocks=(
                blk("PROJECT: ENTRY-CABINET-001", 0.08, 0.05),
                blk("UNITS: MM", 0.08, 0.08),
                blk("OVERALL WIDTH 800", 0.60, 0.11),
                blk("OVERALL HEIGHT 770", 0.60, 0.14),
            ),
        )
    )
    assert not assembly.parts
    assert not assembly.unassociated
    keys = [kv.key for kv in assembly.title_block]
    assert keys == ["PROJECT", "UNITS"]
    types = [c.type for c in assembly.constraints]
    assert types == ["overall_width", "overall_height"]


def test_duplicate_labels_get_distinct_ids() -> None:
    assembly = assemble(
        column(
            ("SHELF - QTY 1", 0.19),
            ("764 X 340 X 18", 0.215),
            ("SHELF - QTY 1", 0.60),
            ("764 X 340 X 18", 0.625),
        )
    )
    assert [part.part_id for part in assembly.parts] == ["shelf", "shelf_2"]


def test_three_value_size_line_needs_no_interpretation_question() -> None:
    assembly = assemble(
        column(
            ("SIDE PANEL - QTY 2", 0.19),
            ("18MM BIRCH PLYWOOD", 0.215),
            ("720 X 380 X 18", 0.24),
        )
    )
    assert not assembly.unassociated
    assert assembly.parts[0].sizes == (720.0, 380.0, 18.0)
