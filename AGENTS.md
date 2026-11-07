# shop-drawings

Pipeline from approved design to fabrication-ready files. The blueprint
"Shop Drawings v1 — Design-to-Fabrication Pipeline" (Obvious project) is the
spec of record; read §3 (architecture) and §5 (verification) before schema work.

## Layout

- `src/shop_drawings/` — package code. `schema.py` is the typed specification
  contract every pipeline stage builds against.
- `tests/fixtures/` — representative spec JSON (the blueprint §3 cabinet).

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
- Conventional commits (`feat:`, `fix:`, ...); branch from `main`; PRs only —
  no direct pushes to `main`.
