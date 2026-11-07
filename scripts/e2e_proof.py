"""Run the integrated pipeline end to end and print the evidence.

The E2E and golden tests (``tests/golden/``) assert the same facts with
pytest rigor; this script is the human-readable, runnable proof of the
whole loop for demos and CI logs:

    python scripts/e2e_proof.py

Design PDF in → extraction → validation → review flow (the fixture's six
unresolved questions answered through the review HTTP API) → approval →
deterministic generation of the .3dm, DXF cut sheet, and cut list, with
the generated model's dimensions verified against the approved spec.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.golden.run_pipeline import run_full_pipeline


def main() -> int:
    work = Path("output/e2e-proof")
    run = run_full_pipeline(work)

    spec = run.approved_spec
    gen = run.generation

    print("Shop Drawings v1 — end-to-end proof")
    print(f"  work dir:      {run.work_dir.resolve()}")
    print(f"  design PDF:    {run.pdf_path.name}")
    print()
    print("1. Extraction")
    print(f"  parts:              {len(spec.parts)}  ({', '.join(p.id for p in spec.parts)})")
    print(f"  constraints:        {len(spec.constraints)}")
    traced = sum(len(p.dimensions) for p in spec.parts) + len(spec.constraints)
    print(f"  source references:  {traced} dimension citations on the page")
    print()
    print("2. Review gate (via the HTTP API, as the browser UI does)")
    for question, answer in run.review_answers:
        print(f"  Q: {question}")
        print(f"  A: {answer}")
    print(f"  review flow wall time: {run.review_seconds:.2f}s")
    print()
    print("3. Approval")
    print(f"  status:            {spec.status.value}")
    print(f"  unresolved:        {len(spec.unresolved_questions)}")
    print(f"  blockers:          {len(run.approval_issue_messages)}")
    for message in run.approval_issue_messages:
        print(f"    - {message}")
    print()
    print("4. Deterministic generation (no AI in the loop)")
    print(f"  .3dm model:   {gen.model_path.name if gen.model_path else '-'}")
    print(f"  DXF sheet:    {gen.sheet_paths[0].name if gen.sheet_paths else '-'}")
    print(f"  cut list:     {gen.cut_list_path.name if gen.cut_list_path else '-'}")
    for esc in gen.escalations:
        print(f"  escalation:   {esc.part_id} (spec package for a human modeler)")
    for check in gen.checks:
        failures = "; ".join(check.failures + check.notes)
        print(
            f"  [{check.status.value.upper()}] {check.name}{(': ' + failures) if failures else ''}"
        )
    print()

    # Programmatic verification: every modeled part must match its spec.
    import rhino3dm

    assert gen.model_path is not None
    model = rhino3dm.File3dm.Read(str(gen.model_path))
    print("5. Dimension verification (model vs approved spec)")
    ok = True
    for obj in model.Objects:
        name = obj.Attributes.Name
        part = next((p for p in spec.parts if p.id == name), None)
        if part is None:
            continue
        bbox = obj.Geometry.GetBoundingBox()
        dims = {
            "length": round(bbox.Max.X - bbox.Min.X, 3),
            "width": round(bbox.Max.Y - bbox.Min.Y, 3),
            "thickness": round(bbox.Max.Z - bbox.Min.Z, 3),
        }
        expected = {k.value: float(v.value) for k, v in part.dimensions.items() if k.value in dims}
        match = all(abs(dims[k] - expected[k]) < 1e-6 for k in expected)
        user_strings = dict(obj.Attributes.GetUserStrings())
        match = match and user_strings.get("spec_status") == "approved"
        ok = ok and match
        state = "ok" if match else f"MISMATCH {dims} != {expected}"
        print(f"  {name}: {state}")
    print()
    print("END-TO-END PROOF: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
