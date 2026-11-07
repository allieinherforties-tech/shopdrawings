# shop-drawings

The integrated design-to-fabrication pipeline: an approved or redlined design
goes in, a **reviewed spec** crosses the gate, and fabrication files (Rhino
`.3dm`, DXF cut sheets, cut list) come out. AI reads and extracts; a human
approves; deterministic code draws.

## How it fits together

Four layers, one artifact type at each boundary:

- **Extraction** (`extraction/`) reads the design — page-aware PDF text plus
  rendered pages, deterministic parsing, optional vision-assisted callout
  reading — and emits a `FurnitureSpec`: typed, source-traceable, with a
  confidence score per value and explicit `unresolved_questions` where the
  drawing is ambiguous.
- **Validation** (`validation/`) runs the blueprint §5 structural checks on
  every spec: traceability, conflict grouping, constraint closure, geometric
  sanity. `approval_blockers` composes those with the schema's own review
  gate so one call decides whether a spec may be approved.
- **Review** (`review/`) is the human gate: a FastAPI app + browser UI that
  surfaces blocked questions, conflicts, and low-confidence values with their
  source crops, records corrections to an append-only review log, and writes
  the approved spec.
- **Generation** (`generation/`) is deterministic: rhino3dm builds the model
  and stamps fabrication metadata; ezdxf writes DXF cut sheets; unsupported
  geometry escalates with a complete package instead of guessed geometry.
  Every job runs verification checks; a failed check removes every file the
  job wrote.

## Schema at a glance

| Model | Carries |
| --- | --- |
| `Project` | job name, units (mm default), source design file |
| `Part` | id, type, quantity, material, typed dimensions, edge treatment, features |
| `Dimension` | value + meaning (`DimensionKind`: overall vs clear vs hole diameter vs setback, ...), tolerance, source, confidence, review status |
| `Feature` | holes, shelf-pin patterns, rebates, dadoes, mortise & tenon — sized by typed dimensions |
| `Constraint` | assembly-level requirements with tolerances (e.g. `overall_width` 800 ± 1) |
| `SourceReference` | page + normalized region + extracted text — where every value came from |
| `FurnitureSpec` | the whole document, with review status and `unresolved_questions` |

Every value must trace to a source reference — an untraceable dimension cannot
be constructed. Unknown kinds and types (a typo'd dimension kind, feature,
part, constraint, or edge) are validation errors by design.

## Generation

`shop_drawings.generation` is the deterministic engine that turns an approved
spec into fabrication files — no AI in the loop, no fabricated geometry.
`generate(spec, config, output_dir)` runs the gate, the writers, and the
verification checks in one pass:

- **Gate.** Only a spec that validates as `approved` with no open questions
  reaches generation; anything else raises `NotApprovedError`.
- **`.3dm` model.** `rhino3dm` writes parts as breps — named objects on the
  configured layers, with fabrication metadata (material, part type, declared
  dimensions, feature parameters) stamped as user strings. Drill and pocket
  features are generated per the panel coordinate convention documented in
  `src/shop_drawings/generation/geometry.py`.
- **DXF cut sheets.** `ezdxf` writes one 2D profile per part — millimeter
  units, closed contours, CUT/POCKET/DRILL layer convention, checked against
  the configured sheet stock (part must fit a sheet with margins).
- **Cut list.** A CSV of every spec part — resolved parts with planned
  dimensions, hardware included, escalated parts flagged with their package
  reference.
- **Escalation.** A part the v1 panel-and-case primitive library cannot
  resolve emits an escalation package (full spec, blocking reasons, the part's
  declared extents as dimensioned orthographic views) instead of guessed
  geometry. The pipeline still completes; the cut list covers the part.
- **Verification.** Every job runs traceability, geometric-sanity, DXF
  fabrication checks, and a round-trip check that regenerates the model and
  compares content digests. A failed check raises `FabricationError` and
  removes every file the job wrote.

**Round-trip reproducibility.** rhino3dm 8.35 embeds random object UUIDs and
varying archive metadata, so byte-identical `.3dm` files are unattainable;
the shipped check compares a content digest — names, layers, user strings,
geometry bounds — instead of raw bytes. A generated file that is hand-edited
in Rhino fails the digest match.

**Studio conventions live in `GenerationConfig`** — layer names, sheet stock,
margins, naming patterns, Rhino archive version — never hardcoded. Units come
from the spec's declared project units (millimeters by default).

## Usage

```python
from shop_drawings import FurnitureSpec
from shop_drawings.generation import generate
from shop_drawings.generation.config import GenerationConfig

spec = FurnitureSpec.model_validate_json(raw_json)
print(spec.approval_errors())  # what blocks approval; [] means the gate passes

result = generate(spec, GenerationConfig(), output_dir)  # raises if not approved
# result.model_path (.3dm), result.sheet_paths (DXF), result.cut_list_path (CSV)
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                                   # all layers + E2E + golden verify
ruff check . && ruff format --check .
mypy src tests
python scripts/e2e_proof.py              # the whole loop, evidence printed
```

### Verification suite

- **`tests/golden/test_e2e_pipeline.py`** — the full loop on the ReportLab
  cabinet fixture: design PDF → extraction → validation → review flow (the
  fixture's six unresolved questions answered through the review HTTP API) →
  approval → generation, with the model's dimensions verified programmatically.
- **`tests/golden/test_golden_files.py`** — pins the approved spec JSON, the
  `.3dm` content digest, the DXF sheet (writer volatiles normalized — see
  `tests/golden/dxf_digest.py`), and the cut list; every future run must
  reproduce them. Re-pin deliberately:
  `SHOP_DRAWINGS_REGEN_GOLDEN=1 pytest tests/golden/test_golden_files.py`.
- **`tests/negative/test_blocked_paths.py`** — the four blocked paths: an
  untraceable value, an unresolved question on approval, a failing closure
  check, and a not-approved spec each stop at their own layer with the right
  message.
- **`scripts/e2e_proof.py`** — the runnable proof for demos and CI logs.
- CI (`.github/workflows/ci.yml`) runs lint, strict types, the full suite,
  the E2E proof, and the golden verify on every push and PR.
