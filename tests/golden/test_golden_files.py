"""Golden files: the pipeline's four artifact types are pinned and must reproduce.

The approved spec JSON, the .3dm content digest, the DXF cut sheet, and the
cut list are pinned under ``files/``. Every future run of the pipeline on
the cabinet fixture must reproduce them; a diff means extraction, review, or
generation drifted. Set ``SHOP_DRAWINGS_REGEN_GOLDEN=1`` to re-pin after a
deliberate change. The .3dm is pinned by content digest (rhino3dm embeds
random UUIDs, so bytes differ per run); the DXF by canonical text (writer
GUIDs and timestamps normalized); spec and cut list by exact bytes.
"""

from __future__ import annotations

import os
from pathlib import Path

from shop_drawings.generation.verify import digest_model
from tests.golden.dxf_digest import canonical_dxf_text
from tests.golden.run_pipeline import PipelineRun

GOLDEN_DIR = Path(__file__).parent / "files"
REGEN = os.environ.get("SHOP_DRAWINGS_REGEN_GOLDEN") == "1"


def _pin_or_compare_bytes(name: str, content: bytes) -> None:
    """Pin ``content`` as a golden file, or assert a future run reproduces it."""
    path = GOLDEN_DIR / name
    if REGEN:
        GOLDEN_DIR.mkdir(exist_ok=True)
        path.write_bytes(content)
    else:
        assert path.exists(), (
            f"golden file {name} is missing — pin it by running "
            "SHOP_DRAWINGS_REGEN_GOLDEN=1 pytest tests/golden/test_golden_files.py"
        )
        assert path.read_bytes() == content, (
            f"output differs from the pinned golden file {name} — the pipeline drifted"
        )


def test_approved_spec_is_reproduced_exactly(pipeline_run: PipelineRun) -> None:
    """The approved spec JSON — the single source of truth — is byte-stable."""
    approved = pipeline_run.approved_spec_path.read_bytes()
    assert b'"status": "approved"' in approved
    _pin_or_compare_bytes("approved-spec.json", approved)


def test_model_content_digest_is_reproduced(pipeline_run: PipelineRun) -> None:
    """The .3dm regenerates identically by content digest, UUIDs aside."""
    assert pipeline_run.generation.model_path is not None
    digest = digest_model(pipeline_run.generation.model_path) + "\n"
    _pin_or_compare_bytes("model.content-digest.txt", digest.encode("utf-8"))


def test_dxf_sheet_is_reproduced(pipeline_run: PipelineRun) -> None:
    """The DXF cut sheet reproduces with writer volatiles normalized away."""
    assert pipeline_run.generation.sheet_paths, "expected at least one cut sheet"
    assert len(pipeline_run.generation.sheet_paths) == 1
    sheet = pipeline_run.generation.sheet_paths[0]
    _pin_or_compare_bytes("sheet-01.dxf", canonical_dxf_text(sheet).encode("utf-8"))


def test_cut_list_is_reproduced_exactly(pipeline_run: PipelineRun) -> None:
    """The cut list CSV is byte-stable."""
    assert pipeline_run.generation.cut_list_path is not None
    _pin_or_compare_bytes(
        "entry-cabinet-001_cutlist.csv", pipeline_run.generation.cut_list_path.read_bytes()
    )


def test_roundtrip_digest_independent_of_run_order(
    pipeline_run: PipelineRun, tmp_path: Path
) -> None:
    """Regenerating from the same approved spec in a fresh directory reproduces
    the pinned digest — proving no hand-edits and no run-to-run drift."""
    from shop_drawings.generation import generate

    regeneration = generate(pipeline_run.approved_spec, out_dir=tmp_path)
    assert regeneration.model_path is not None
    pinned = GOLDEN_DIR / "model.content-digest.txt"
    if not REGEN and pinned.exists():
        assert digest_model(regeneration.model_path) == pinned.read_text(encoding="utf-8").strip()
