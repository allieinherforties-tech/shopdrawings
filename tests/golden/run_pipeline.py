"""The full-pipeline proof: design PDF in, fabrication files out, review in between.

This is the integrated pipeline's end-to-end orchestrator. It runs the real
layers in order — extraction, validation, the review-gate HTTP API, approval,
deterministic generation — and records everything the run produced so the
E2E test, the golden-file tests, and the CI proof script all exercise the
same path. The six fixture questions are answered through the review API,
as a reviewer would; no spec field is edited by hand at any point.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from shop_drawings.extraction import extract_spec
from shop_drawings.generation import generate
from shop_drawings.review.app import create_app
from shop_drawings.schema import FurnitureSpec
from shop_drawings.validation import validate_spec
from tests.fixtures.cabinet_pdf import create_cabinet_pdf

PROJECT_NAME = "entry-cabinet-001"

#: The reviewer's plain-language answers to the fixture's blocked questions,
#: keyed by what the question is about. Real answers a reviewer gives from
#: knowledge of the job — recorded in the review log verbatim.
_ANSWERS: tuple[tuple[str, str], ...] = (
    (
        "REVEAL 3",
        "The 3 mm is the door reveal; it dimensions no fabricated part — noted, no spec change.",
    ),
    (
        "500",
        "The bare 500 is a shelf-span reference on the drawing; no part carries it — "
        "noted, no spec change.",
    ),
    (
        "two-value size line",
        "Confirmed against the drawing: first number is the length run, second the width.",
    ),
)


def answer_for(question: str) -> str:
    """The reviewer's answer for one blocked question."""
    for needle, answer in _ANSWERS:
        if needle in question:
            return answer
    return "Confirmed against the drawing."


@dataclass(frozen=True)
class PipelineRun:
    """Everything one full-pipeline run produced, in run order."""

    work_dir: Path
    pdf_path: Path
    draft_spec: FurnitureSpec
    draft_issue_messages: tuple[str, ...]
    review_answers: tuple[tuple[str, str], ...]
    review_seconds: float
    approved_spec_path: Path
    approved_spec: FurnitureSpec
    approval_issue_messages: tuple[str, ...]
    generation: Any  # GenerationResult — typed loosely to keep tests import-light
    escalation_dirs: tuple[Path, ...]


def _all_value_addresses(spec_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every dimension address in a spec payload, as JSON-shaped dicts."""
    addresses: list[dict[str, Any]] = []
    for part in spec_data["parts"]:
        for kind in part["dimensions"]:
            addresses.append({"part_id": part["id"], "feature_index": None, "kind": kind})
        for index, feature in enumerate(part["features"]):
            for kind in feature["dimensions"]:
                addresses.append({"part_id": part["id"], "feature_index": index, "kind": kind})
    return addresses


def run_full_pipeline(work_dir: Path) -> PipelineRun:
    """Prove the loop: extract → validate → review → approve → generate.

    Every step runs the real layer; the review flow goes through the HTTP
    API exactly as the browser UI does.
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Extraction: the ReportLab cabinet drawing in, a draft spec out.
    pdf_path = create_cabinet_pdf(work_dir / f"{PROJECT_NAME}.pdf")
    extracted = extract_spec(pdf_path, project_name=PROJECT_NAME)
    draft = extracted.spec
    draft_report = validate_spec(draft)
    draft_issue_messages = tuple(
        f"[{issue.rule.value}] {issue.message}" for issue in draft_report.issues
    )

    # 2. Review: the draft spec file is the review session's source of truth.
    spec_path = work_dir / "spec.json"
    spec_path.write_text(draft.model_dump_json(indent=2) + "\n", encoding="utf-8")
    client = TestClient(create_app(spec_path))

    answers: list[tuple[str, str]] = []
    review_started = time.perf_counter()
    while True:
        payload = client.get("/api/review")
        assert payload.status_code == 200, payload.text
        queue = payload.json()["queue"]
        if not queue["questions"]:
            break
        question = queue["questions"][0]
        response = client.post(
            "/api/questions/resolve", json={"index": 0, "answer": answer_for(question)}
        )
        assert response.status_code == 200, response.text
        answers.append((question, answer_for(question)))

    # 3. Batch-confirm every remaining value, part, and constraint, then approve.
    payload = client.get("/api/review")
    spec_data: dict[str, Any] = payload.json()["spec"]
    confirm = client.post(
        "/api/values/confirm-batch",
        json={
            "addresses": _all_value_addresses(spec_data),
            "part_ids": [part["id"] for part in spec_data["parts"]],
            "constraint_indexes": list(range(len(spec_data["constraints"]))),
        },
    )
    assert confirm.status_code == 200, confirm.text
    approve = client.post("/api/approve")
    assert approve.status_code == 200, approve.text
    review_seconds = time.perf_counter() - review_started

    approved = FurnitureSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    approval_report = validate_spec(approved)
    approval_issue_messages = tuple(
        f"[{issue.rule.value}] {issue.message}" for issue in approval_report.issues
    )

    # 4. Generation: deterministic files from the approved spec, checks included.
    generation = generate(approved, out_dir=work_dir / "fabrication")
    escalation_dirs = tuple(
        Path(work_dir / "fabrication" / "escalation" / esc.part_id)
        for esc in generation.escalations
    )

    return PipelineRun(
        work_dir=work_dir,
        pdf_path=pdf_path,
        draft_spec=draft,
        draft_issue_messages=draft_issue_messages,
        review_answers=tuple(answers),
        review_seconds=review_seconds,
        approved_spec_path=spec_path,
        approved_spec=approved,
        approval_issue_messages=approval_issue_messages,
        generation=generation,
        escalation_dirs=escalation_dirs,
    )
