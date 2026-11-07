# shop-drawings

Pipeline from approved design to fabrication-ready files. The blueprint
"Shop Drawings v1 — Design-to-Fabrication Pipeline" (Obvious project) is the
spec of record; read §3 (architecture) and §5 (verification) before schema work.

## Layout

- `src/shop_drawings/` — package code. `schema.py` is the typed specification
  contract every pipeline stage builds against; `generation/` is the
  deterministic generation engine (config, geometry primitives, .3dm and DXF
  writers, cut list, escalation packages, verification checks, entry point).
- `tests/fixtures/` — representative spec JSON (the blueprint §3 cabinet).
- `tests/_builders.py` — shared spec/model helpers, including the typed
  rhino3dm stub workarounds; use these instead of new `type: ignore` lines.

## Commands

```bash
pip install -e ".[dev]"                 # set up (use a venv)
pytest                                  # tests
ruff check . && ruff format --check .   # lint
mypy src tests                          # types (strict)
```

## Rules

- **The review gate is enforced in the schema.** A spec marked `approved` with
  unresolved questions or unreviewed values must fail validation. Do not weaken
  it — weakening it is a user-level decision.
- **Every value is traceable.** Dimensions and constraints carry a source
  reference (page + region + text); untraceable values are unconstructible by
  design.
- **Unknown kinds and types are validation errors by design.** Extend the enums
  deliberately as the primitive library grows; never loosen them to make a bad
  extraction parse.
- **Generation is deterministic.** No AI calls and no inferred geometry in
  `generation/` — a part the primitive library cannot resolve escalates with a
  complete package instead. Studio conventions (layers, sheet stock, naming)
  belong in `GenerationConfig`, never hardcoded.
- **Generated files are never hand-edited.** Corrections go into the spec and
  regenerate; the round-trip digest check enforces this on every job.
- Conventional commits (`feat:`, `fix:`, ...); branch from `main`; PRs only —
  no direct pushes to `main`.
