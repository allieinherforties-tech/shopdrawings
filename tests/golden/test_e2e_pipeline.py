"""The end-to-end proof: ReportLab cabinet drawing in, fabrication files out.

Runs the integrated pipeline through every layer — extraction, validation,
the review-gate HTTP API, approval, deterministic generation — and verifies
the produced files programmatically, dimension by dimension.
"""

from __future__ import annotations

import csv
import io
import json

from ezdxf.entities.lwpolyline import LWPolyline
from ezdxf.filemanagement import readfile

from shop_drawings.generation.verify import CheckStatus, rhino3dm_read
from shop_drawings.schema import ReviewStatus
from tests.golden.evaluate import evaluate_extraction, load_expectations
from tests.golden.run_pipeline import PipelineRun

EXPECTED_PARTS = ["side_panel", "shelf", "top", "back_panel"]


def test_extraction_reads_the_fixture_completely(pipeline_run: PipelineRun) -> None:
    """Draft spec: the four drawn parts, both constraints, six honest questions."""
    spec = pipeline_run.draft_spec
    assert [part.id for part in spec.parts] == EXPECTED_PARTS
    assert [constraint.type.value for constraint in spec.constraints] == [
        "overall_width",
        "overall_height",
    ]
    assert len(spec.unresolved_questions) == 6


def test_every_extracted_value_is_traced_to_the_page(pipeline_run: PipelineRun) -> None:
    """Traceability: page and region on every dimension everywhere in the spec."""
    for part in pipeline_run.draft_spec.parts:
        for dim in part.dimensions.values():
            assert dim.source.page >= 1, part.id
            assert len(dim.source.region) == 4, part.id
        for feature in part.features:
            for dim in feature.dimensions.values():
                assert dim.source.page >= 1, (part.id, feature.type)
                assert len(dim.source.region) == 4, (part.id, feature.type)
        for constraint in pipeline_run.draft_spec.constraints:
            assert constraint.source.page >= 1
            assert len(constraint.source.region) == 4


def test_validation_surfaces_the_height_closure_with_both_values(
    pipeline_run: PipelineRun,
) -> None:
    """The drawn 770 cannot close (closest achievable 772) — shown, not hidden."""
    closure = [m for m in pipeline_run.draft_issue_messages if "constraint_closure" in m]
    assert closure, pipeline_run.draft_issue_messages
    assert "770" in closure[0] and "772" in closure[0]


def test_review_flow_answers_all_six_questions_through_the_api(
    pipeline_run: PipelineRun,
) -> None:
    """Every blocked question resolved by a recorded reviewer answer."""
    assert len(pipeline_run.review_answers) == 6
    assert all(answer for _, answer in pipeline_run.review_answers)


def test_approval_leaves_a_fully_approved_spec(pipeline_run: PipelineRun) -> None:
    """Post-approval state: status approved, zero open questions, every value approved."""
    spec = pipeline_run.approved_spec
    assert spec.status == ReviewStatus.APPROVED
    assert spec.unresolved_questions == []
    for part in spec.parts:
        assert part.status == ReviewStatus.APPROVED, part.id
        assert all(d.status == ReviewStatus.APPROVED for d in part.dimensions.values())
        for feature in part.features:
            assert all(d.status == ReviewStatus.APPROVED for d in feature.dimensions.values()), (
                part.id,
                feature.type,
            )
    assert all(c.status == ReviewStatus.APPROVED for c in spec.constraints)


def test_generation_ships_modeled_parts_escalates_the_rest(pipeline_run: PipelineRun) -> None:
    """Three parts fabricate; the side panel escalates with a complete package."""
    result = pipeline_run.generation
    assert result.model_path is not None and result.model_path.exists()
    assert len(result.sheet_paths) == 1 and result.sheet_paths[0].exists()
    assert result.cut_list_path is not None and result.cut_list_path.exists()

    assert len(result.escalations) == 1
    escalation = result.escalations[0]
    assert escalation.part_id == "side_panel"
    escalation_dir = pipeline_run.escalation_dirs[0]
    assert (escalation_dir / "package.json").exists()
    assert (escalation_dir / "views.dxf").exists()
    package = json.loads((escalation_dir / "package.json").read_text(encoding="utf-8"))
    assert package["part_id"] == "side_panel"
    assert package["reasons"], "escalation must state why it was not fabricated"


def test_generation_checks_pass_and_skip_closure_loudly(pipeline_run: PipelineRun) -> None:
    """Every check that ran passes; the skipped closure check states why it is skipped."""
    checks = pipeline_run.generation.checks
    assert checks, "generation must report its automatic checks"
    for check in checks:
        if check.status == CheckStatus.SKIPPED:
            assert "closure" in check.name.lower()
            assert check.notes, "a skipped check must say why it did not run"
        else:
            assert check.status == CheckStatus.PASS, check.failures
            assert not check.failures


def test_model_dimensions_match_the_approved_spec(pipeline_run: PipelineRun) -> None:
    """Programmatic verification: each brep's size equals the approved spec values."""
    model = rhino3dm_read(pipeline_run.generation.model_path)
    objects = {
        obj.Attributes.Name: obj
        for obj in model.Objects  # type: ignore[attr-defined]  # stub omits __iter__; runtime iterates
    }
    expected_sizes = {
        "shelf": (764.0, 340.0, 18.0),
        "top": (800.0, 380.0, 18.0),
        "back_panel": (764.0, 720.0, 12.0),
    }
    assert set(objects) == set(expected_sizes)
    for name, (length, width, thickness) in expected_sizes.items():
        bbox = objects[name].Geometry.GetBoundingBox()
        assert length == (bbox.Max.X - bbox.Min.X), name
        assert width == (bbox.Max.Y - bbox.Min.Y), name
        assert thickness == (bbox.Max.Z - bbox.Min.Z), name
        attributes = objects[name].Attributes
        user_strings = dict(attributes.GetUserStrings())
        assert user_strings["spec_status"] == "approved", name
        assert user_strings["material"] == "birch plywood", name


def test_dxf_sheet_fabrication_structure_matches_the_spec(
    pipeline_run: PipelineRun,
) -> None:
    """Cut-sheet invariants: mm units, closed outlines, each part at its declared size."""
    sheet = pipeline_run.generation.sheet_paths[0]
    doc = readfile(str(sheet))
    assert doc.header.get("$INSUNITS") == 4, "cut sheet must be in millimetres"

    outlines: dict[tuple[float, float], int] = {}
    for entity in doc.modelspace().query("LWPOLYLINE"):
        if not isinstance(entity, LWPolyline):
            continue
        if entity.dxf.layer != "CUT":
            continue
        assert entity.closed, "every CUT contour must be a closed loop"
        points = list(entity.get_points())
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        size = (round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3))
        outlines[size] = outlines.get(size, 0) + 1

    expected = {
        (764.0, 340.0): 1,  # shelf
        (800.0, 380.0): 1,  # top
        (764.0, 720.0): 1,  # back panel
    }
    assert outlines == expected


def test_cut_list_rows_match_the_approved_spec(pipeline_run: PipelineRun) -> None:
    """Cut list: one row per part, escalated parts flagged, dimensions exact."""
    cut_list = pipeline_run.generation.cut_list_path
    assert cut_list is not None
    rows = list(csv.DictReader(io.StringIO(cut_list.read_text(encoding="utf-8"))))
    assert len(rows) == 4

    by_id = {row["part_id"]: row for row in rows}
    assert by_id["shelf"]["length"] == "764"
    assert by_id["shelf"]["width"] == "340"
    assert by_id["shelf"]["thickness"] == "18"
    assert by_id["shelf"]["quantity"] == "3"
    assert by_id["top"]["edge_banding"] == "front"
    assert by_id["back_panel"]["thickness"] == "12"
    assert "escalated" in by_id["side_panel"]["features"]
    assert "escalation/side_panel/package.json" in by_id["side_panel"]["features"]


def test_golden_evaluation_metrics_are_perfect_on_the_fixture(
    pipeline_run: PipelineRun,
) -> None:
    """Blueprint §5: extraction and association accuracy of 1.0, six questions."""
    evaluation = evaluate_extraction(
        pipeline_run.draft_spec, load_expectations(), pipeline_run.review_seconds
    )
    assert evaluation.extraction_accuracy == 1.0, evaluation.missed_values
    assert evaluation.association_accuracy == 1.0, evaluation.misassociated_values
    assert evaluation.unexpected_values == (), evaluation.unexpected_values
    assert evaluation.question_mismatches == ()
    assert evaluation.questions_per_job == 6
