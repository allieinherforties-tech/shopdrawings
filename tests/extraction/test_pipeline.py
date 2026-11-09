"""End-to-end: design files in, schema-valid specs out, questions where due."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from shop_drawings.extraction import DesignDocument, Region, VisionCallout, extract_spec
from shop_drawings.extraction.vision import VisionError
from shop_drawings.schema import (
    DimensionKind,
    Edge,
    FurnitureSpec,
    ReviewStatus,
    TracedValue,
    Units,
)


def test_fixture_pdf_yields_a_schema_valid_spec(cabinet_pdf: Path) -> None:
    result = extract_spec(cabinet_pdf)
    FurnitureSpec.model_validate(result.spec.model_dump())  # raises if invalid
    assert result.spec.status is ReviewStatus.NEEDS_REVIEW


def test_fixture_parts_are_complete(cabinet_pdf: Path) -> None:
    result = extract_spec(cabinet_pdf)
    spec = result.spec
    assert [part.id for part in spec.parts] == ["side_panel", "shelf", "top", "back_panel"]

    side = spec.parts[0]
    assert side.quantity == 2
    assert side.material == "birch plywood"
    assert side.dimensions[DimensionKind.LENGTH].value == 720.0
    assert side.dimensions[DimensionKind.WIDTH].value == 380.0
    assert side.dimensions[DimensionKind.DEPTH].value == 380.0
    assert side.dimensions[DimensionKind.SETBACK].value == 37.0

    shelf = spec.parts[1]
    assert shelf.quantity == 3
    assert shelf.dimensions[DimensionKind.LENGTH].value == 764.0

    top = spec.parts[2]
    assert top.edge_banding == [Edge.FRONT]

    back = spec.parts[3]
    assert back.material == "birch plywood"
    assert back.dimensions[DimensionKind.THICKNESS].value == 12.0


def test_fixture_hole_feature_is_typed(cabinet_pdf: Path) -> None:
    result = extract_spec(cabinet_pdf)
    side = result.spec.parts[0]
    assert len(side.features) == 1
    feature = side.features[0]
    assert feature.type == "shelf_pin_holes"
    assert feature.dimensions[DimensionKind.HOLE_DIAMETER].value == 5.0
    assert feature.dimensions[DimensionKind.SPACING].value == 32.0


def test_fixture_constraints_and_title_block(cabinet_pdf: Path) -> None:
    result = extract_spec(cabinet_pdf)
    spec = result.spec
    assert {c.type for c in spec.constraints} == {"overall_width", "overall_height"}
    assert spec.project.name == "ENTRY-CABINET-001"
    assert spec.project.units is Units.MM
    assert spec.project.source == cabinet_pdf.name


def test_fixture_questions_cover_the_unplaceable(cabinet_pdf: Path) -> None:
    result = extract_spec(cabinet_pdf)
    questions = "\n".join(result.spec.unresolved_questions)
    # Four two-value size lines, one bare number, one floating dimension.
    assert len(result.spec.unresolved_questions) == 6
    assert "500" in questions  # the bare number, named for what it is
    assert "REVEAL 3" in questions  # the floating tagged dimension


def test_every_value_traces_to_the_source(cabinet_pdf: Path) -> None:
    result = extract_spec(cabinet_pdf)
    spec = result.spec
    traced: list[TracedValue] = [d for part in spec.parts for d in part.dimensions.values()]
    traced += [d for part in spec.parts for f in part.features for d in f.dimensions.values()]
    traced += list(spec.constraints)
    assert len(traced) >= 10  # dimensions across four parts plus two constraints
    for value in traced:
        assert value.source.page == 1
        assert value.source.text.strip()
        x0, y0, x1, y1 = value.source.region
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
        assert 0.0 <= value.confidence <= 1.0


def test_project_name_override(cabinet_pdf: Path) -> None:
    result = extract_spec(cabinet_pdf, project_name="entry-cabinet-001")
    assert result.spec.project.name == "entry-cabinet-001"


def _png_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (120, 90), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_image_input_takes_the_vision_path(tmp_path: Path, stub_vision: Callable[..., Any]) -> None:
    image = tmp_path / "sketch.png"
    image.write_bytes(_png_bytes())
    client = stub_vision(
        VisionCallout(
            text="DOOR - QTY 2",
            page=1,
            region=(0.05, 0.10, 0.30, 0.12),
            role="part_label",
            part_label="DOOR",
            confidence=0.88,
        ),
        VisionCallout(
            text="18MM OAK",
            page=1,
            region=(0.05, 0.13, 0.30, 0.15),
            role="material",
            part_label=None,
            confidence=0.80,
        ),
        VisionCallout(
            text="396 X 296",
            page=1,
            region=(0.05, 0.16, 0.30, 0.18),
            role=None,
            part_label=None,
            confidence=0.75,
        ),
    )
    result = extract_spec(image, vision_client=client)
    spec = result.spec
    assert [part.id for part in spec.parts] == ["door"]
    door = spec.parts[0]
    assert door.quantity == 2
    assert door.material == "oak"
    # Vision confidence caps the parse confidence: sizes 0.85 parsed, 0.75 seen.
    assert door.dimensions[DimensionKind.LENGTH].confidence <= 0.75
    assert door.dimensions[DimensionKind.LENGTH].value == 396.0


def test_low_confidence_callout_becomes_a_question(
    tmp_path: Path, stub_vision: Callable[..., Any]
) -> None:
    image = tmp_path / "blurry.png"
    image.write_bytes(_png_bytes())
    client = stub_vision(
        VisionCallout(
            text="700",
            page=1,
            region=(0.40, 0.40, 0.50, 0.45),
            role="width",
            part_label=None,
            confidence=0.30,
        ),
    )
    result = extract_spec(image, vision_client=client)
    spec = result.spec
    assert not spec.parts
    joined = "\n".join(spec.unresolved_questions)
    assert "confidence 0.30" in joined
    assert "700" in joined


def test_vision_role_types_a_bare_number(tmp_path: Path, stub_vision: Callable[..., Any]) -> None:
    image = tmp_path / "annotated.png"
    image.write_bytes(_png_bytes())
    client = stub_vision(
        VisionCallout(
            text="850",
            page=1,
            region=(0.30, 0.30, 0.40, 0.35),
            role="overall_width",
            part_label=None,
            confidence=0.9,
        ),
    )
    result = extract_spec(image, vision_client=client)
    constraint = result.spec.constraints[0]
    assert constraint.type == "overall_width"
    assert constraint.value == 850.0
    assert constraint.confidence == 0.9


def test_vision_upgrade_caps_confidence_at_the_model_s(
    cabinet_pdf: Path, stub_vision: Callable[..., Any]
) -> None:
    # Re-read the in-column "DEPTH 380" line with a generous page-wide region:
    # the text layer keeps its precise region, the confidence becomes the
    # model's 0.70 (below the parser's 0.95).
    client = stub_vision(
        VisionCallout(
            text="DEPTH 380",
            page=1,
            region=(0.0, 0.0, 1.0, 1.0),
            role="depth",
            part_label=None,
            confidence=0.70,
        ),
    )
    result = extract_spec(cabinet_pdf, vision_client=client)
    side = result.spec.parts[0]
    depth = side.dimensions[DimensionKind.DEPTH]
    assert depth.value == 380.0
    assert depth.confidence == 0.70


def test_document_with_no_read_content_asks_instead_of_lying(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "memo.pdf"
    doc = canvas.Canvas(str(pdf))
    doc.drawString(72, 700, "Welcome to the studio handbook for new makers")
    doc.save()
    result = extract_spec(pdf)
    assert not result.spec.parts
    assert any("No fabrication content" in q for q in result.spec.unresolved_questions)


class _FailingVisionClient:
    """A ``VisionClient`` whose API always fails — no network."""

    def extract(self, document: DesignDocument) -> tuple[VisionCallout, ...]:
        raise VisionError("anthropic: api error 500 (overloaded)")


def test_vision_api_error_degrades_to_text_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    image = tmp_path / "sketch.png"
    image.write_bytes(_png_bytes())
    with caplog.at_level(logging.WARNING, logger="shop_drawings.extraction.pipeline"):
        result = extract_spec(image, vision_client=_FailingVisionClient())
    # The upload still produces a spec — the text-only path, questions intact.
    assert not result.spec.parts
    assert any("No fabrication content" in q for q in result.spec.unresolved_questions)
    assert result.vision is not None
    assert result.vision.error is not None
    assert "VisionError" in result.vision.error
    assert any("degraded" in record.message for record in caplog.records)


def test_high_confidence_vision_reading_proposes_a_traced_value(
    tmp_path: Path, stub_vision: Callable[..., Any]
) -> None:
    image = tmp_path / "sheet.png"
    image.write_bytes(_png_bytes())
    client = stub_vision(
        VisionCallout(
            text="DOOR - QTY 2",
            page=1,
            region=(0.05, 0.10, 0.30, 0.12),
            role="part_label",
            part_label="DOOR",
            confidence=0.88,
        ),
        VisionCallout(
            text="18MM OAK",
            page=1,
            region=(0.05, 0.13, 0.30, 0.15),
            role="material",
            part_label=None,
            confidence=0.80,
        ),
        VisionCallout(
            text="396 X 296",
            page=1,
            region=(0.05, 0.16, 0.30, 0.18),
            role=None,
            part_label=None,
            confidence=0.92,
        ),
    )
    result = extract_spec(image, vision_client=client)
    door = result.spec.parts[0]
    dim = door.dimensions[DimensionKind.LENGTH]
    assert dim.value == 396.0
    # Traceability holds for vision-proposed values: page, region, and the
    # exact text the model read. The cited crop is the read region, padded.
    assert dim.source.page == 1
    assert dim.source.text == "396 X 296"
    expected = Region(0.05, 0.16, 0.30, 0.18).padded().as_tuple()
    assert dim.source.region == pytest.approx(expected)


def test_variant_callout_becomes_a_question_offering_the_variants(
    tmp_path: Path, stub_vision: Callable[..., Any]
) -> None:
    image = tmp_path / "product-sheet.png"
    image.write_bytes(_png_bytes())
    client = stub_vision(
        VisionCallout(
            text='H 22" OR 24"',
            page=1,
            region=(0.30, 0.30, 0.50, 0.35),
            role="height",
            part_label=None,
            confidence=0.95,
        ),
    )
    result = extract_spec(image, vision_client=client)
    # Neither variant was picked: no dimension, no constraint, only a question.
    assert not result.spec.parts
    assert not result.spec.constraints
    joined = "\n".join(result.spec.unresolved_questions)
    assert 'H 22" OR 24"' in joined
    assert "Which value applies?" in joined


def test_variant_in_a_part_column_is_still_only_a_question(
    tmp_path: Path, stub_vision: Callable[..., Any]
) -> None:
    image = tmp_path / "variant-column.png"
    image.write_bytes(_png_bytes())
    client = stub_vision(
        VisionCallout(
            text="DOOR - QTY 2",
            page=1,
            region=(0.05, 0.10, 0.30, 0.12),
            role="part_label",
            part_label="DOOR",
            confidence=0.88,
        ),
        VisionCallout(
            text='H 22" OR 24"',
            page=1,
            region=(0.05, 0.13, 0.30, 0.15),
            role="height",
            part_label="DOOR",
            confidence=0.95,
        ),
    )
    result = extract_spec(image, vision_client=client)
    door = result.spec.parts[0]
    assert DimensionKind.HEIGHT not in door.dimensions
    assert any("Which value applies?" in q for q in result.spec.unresolved_questions)


def test_vision_observations_record_what_became_of_each_callout(
    cabinet_pdf: Path, stub_vision: Callable[..., Any]
) -> None:
    client = stub_vision(
        # Re-reads the text layer's own "DEPTH 380" line.
        VisionCallout(
            text="DEPTH 380",
            page=1,
            region=(0.0, 0.0, 1.0, 1.0),
            role="depth",
            part_label=None,
            confidence=0.70,
        ),
        # Not in the text layer; above the floor → proposed block.
        VisionCallout(
            text="18MM OAK",
            page=1,
            region=(0.05, 0.90, 0.30, 0.95),
            role="material",
            part_label=None,
            confidence=0.90,
        ),
        # The model itself doubts it → question, never a value.
        VisionCallout(
            text="700",
            page=1,
            region=(0.40, 0.40, 0.50, 0.45),
            role="width",
            part_label=None,
            confidence=0.30,
        ),
    )
    result = extract_spec(cabinet_pdf, vision_client=client)
    assert result.vision is not None
    by_text = {o.text: o.outcome for o in result.vision.observations}
    assert by_text["DEPTH 380"] == "upgraded"
    assert by_text["18MM OAK"] == "proposed"
    assert by_text["700"] == "question"
