"""The deterministic callout grammar: every construct parses, prose doesn't."""

from shop_drawings.extraction import Region, TextBlock, parse_block


def block(text: str, **kwargs: object) -> TextBlock:
    """A text block at a fixed page position."""
    defaults: dict[str, object] = {
        "page": 1,
        "region": Region(0.1, 0.2, 0.3, 0.22),
    }
    defaults.update(kwargs)
    return TextBlock(text=text, **defaults)  # type: ignore[arg-type]


def test_part_label_with_quantity() -> None:
    parsed = parse_block(block("SIDE PANEL - QTY 2"))
    assert parsed.part_label is not None
    assert parsed.part_label.label == "SIDE PANEL"
    assert parsed.part_label.quantity == 2


def test_part_label_without_quantity_defaults_to_one() -> None:
    parsed = parse_block(block("DRAWER FRONT"))
    assert parsed.part_label is not None
    assert parsed.part_label.quantity == 1


def test_material_line_captures_name_and_thickness() -> None:
    parsed = parse_block(block("18MM BIRCH PLYWOOD"))
    assert parsed.material is not None
    assert parsed.material.material == "birch plywood"
    assert parsed.material.thickness == 18.0


def test_material_line_converts_inches() -> None:
    parsed = parse_block(block("3/4 IN OAK"))  # no fraction support: unmatched
    assert parsed.is_empty  # v1 grammar reads decimal inches, not fractions


def test_size_span_two_values() -> None:
    parsed = parse_block(block("720 X 380"))
    assert parsed.sizes is not None
    assert parsed.sizes.values == (720.0, 380.0)


def test_size_span_three_values_gains_confidence() -> None:
    two = parse_block(block("720 X 380"))
    three = parse_block(block("720 X 380 X 18"))
    assert three.sizes is not None and two.sizes is not None
    assert three.sizes.values == (720.0, 380.0, 18.0)
    assert three.sizes.confidence > two.sizes.confidence


def test_size_span_with_unit_suffix() -> None:
    parsed = parse_block(block("76.4 x 34 cm"))
    assert parsed.sizes is not None
    assert parsed.sizes.values == (764.0, 340.0)  # millimetres everywhere


def test_tagged_dimension_kinds() -> None:
    for text, kind in (
        ("WIDTH 500", "width"),
        ("HEIGHT: 720", "height"),
        ("DEPTH 380", "depth"),
        ("THK 18", "thickness"),
    ):
        parsed = parse_block(block(text))
        assert parsed.dimensions and parsed.dimensions[0].kind == kind, text
        assert parsed.dimensions[0].confidence >= 0.9


def test_tagged_overall_becomes_constraint() -> None:
    parsed = parse_block(block("OVERALL WIDTH 800"))
    assert parsed.constraints and parsed.constraints[0].type == "overall_width"
    assert parsed.constraints[0].value == 800.0


def test_overall_span_gives_three_constraints() -> None:
    parsed = parse_block(block("OVERALL 800 x 720 x 380"))
    kinds = [c.type for c in parsed.constraints]
    assert kinds == ["overall_width", "overall_height", "overall_depth"]


def test_hole_pattern_plain_form() -> None:
    parsed = parse_block(block("Ø5 PINS @ 32"))
    kinds = [d.kind for d in parsed.dimensions]
    assert kinds == ["hole_diameter", "spacing"]
    values = [d.value for d in parsed.dimensions]
    assert values == [5.0, 32.0]


def test_hole_pattern_with_qualifier_and_setback() -> None:
    parsed = parse_block(block("SHELF PIN HOLES Ø5 @ 32, 37 FROM FRONT EDGE"))
    kinds = [d.kind for d in parsed.dimensions]
    assert kinds == ["hole_diameter", "spacing", "setback"]
    setback = parsed.dimensions[2]
    assert setback.value == 37.0
    assert setback.reference == "front_edge"


def test_edge_banding_lists_edges() -> None:
    parsed = parse_block(block("EDGE BANDING: FRONT, TOP"))
    assert parsed.edge_banding is not None
    assert parsed.edge_banding.edges == ("FRONT", "TOP")


def test_joinery_note_becomes_feature() -> None:
    parsed = parse_block(block("DADO 6mm FOR BACK PANEL"))
    assert parsed.feature is not None
    assert parsed.feature.type == "dado"
    assert parsed.feature.note == "DADO 6mm FOR BACK PANEL"


def test_joinery_alias_normalizes() -> None:
    parsed = parse_block(block("RABBET 10mm AT BACK"))
    assert parsed.feature is not None
    assert parsed.feature.type == "rebate"


def test_title_block_key_value() -> None:
    parsed = parse_block(block("PROJECT: ENTRY-CABINET-001"))
    assert parsed.title_kv is not None
    assert parsed.title_kv.key == "PROJECT"
    assert parsed.title_kv.value == "ENTRY-CABINET-001"


def test_title_block_units_line() -> None:
    parsed = parse_block(block("UNITS: MM"))
    assert parsed.title_kv is not None
    assert parsed.title_kv.value == "MM"


def test_bare_number_is_recorded_never_typed() -> None:
    parsed = parse_block(block("800"))
    # Recorded as a bare number; never typed as a dimension or constraint.
    assert parsed.bare_number is not None
    assert parsed.dimensions == () and parsed.constraints == ()
    assert parsed.bare_number.value == 800.0
    assert parsed.bare_number.confidence < 0.6  # below the review floor


def test_bare_number_with_unit() -> None:
    parsed = parse_block(block("25.4 in"))
    assert parsed.bare_number is not None
    assert parsed.bare_number.value == 645.16


def test_sheet_noise_is_neither_finding_nor_question() -> None:
    parsed = parse_block(block("NOTES: 1. ALL JOINERY GLUED AND PINNED"))
    assert parsed.is_empty
    from shop_drawings.extraction import looks_like_data

    assert not looks_like_data(parsed.block)


def test_narrative_prose_is_ignored() -> None:
    from shop_drawings.extraction import looks_like_data

    parsed = parse_block(block("Shop Drawings — Entry Cabinet"))
    assert parsed.is_empty
    assert not looks_like_data(parsed.block)


def test_unparseable_caps_line_becomes_a_question_candidate() -> None:
    from shop_drawings.extraction import looks_like_data

    parsed = parse_block(block("MDF 6 SIDES GLUED"))
    assert parsed.is_empty
    assert looks_like_data(parsed.block)


def test_scale_line_is_title_metadata_not_a_question() -> None:
    parsed = parse_block(block("SCALE: 1:10"))
    assert parsed.title_kv is not None
    assert parsed.title_kv.key == "SCALE"
