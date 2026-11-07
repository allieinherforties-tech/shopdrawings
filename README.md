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

## Usage

```python
from shop_drawings import FurnitureSpec

spec = FurnitureSpec.model_validate_json(raw_json)
print(spec.approval_errors())  # what blocks approval; [] means the gate passes
encoded = spec.model_dump_json()  # round-trips losslessly
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
mypy src tests
```
