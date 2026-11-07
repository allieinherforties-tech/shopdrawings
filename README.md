# shop-drawings

Typed furniture specifications for the studio's design-to-fabrication pipeline:
an approved or redlined design goes in, a **reviewed spec** crosses the gate,
and fabrication files (Rhino `.3dm`, DXF cut sheets) come out.

This package ships v1 of the **furniture specification schema** — the typed
contract every other pipeline stage (extraction, validation, review UI,
generation) builds against. The blueprint "Shop Drawings v1 —
Design-to-Fabrication Pipeline" is the spec of record.

## How it fits together

- **AI never draws fabrication geometry.** Extraction reads the design and
  emits a `FurnitureSpec`: typed, source-traceable, with a confidence score
  per value and explicit `unresolved_questions` where the drawing is ambiguous.
- **A human approves the spec.** The schema enforces the review gate itself:
  a spec marked `approved` while questions are open or values are unreviewed
  fails validation.
- **Code generates the files.** Generation draws every output from the
  approved spec — the spec is the single source of truth.

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
pytest
ruff check . && ruff format --check .
mypy src tests
```
