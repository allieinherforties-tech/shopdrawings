"""The generation entry point: approved spec in, fabrication files out.

``generate`` is the only entry the pipeline documents. It re-enforces the
review gate, resolves each part through the primitive library, writes the
.3dm model, the DXF cut sheets, and the cut list, then runs the blueprint
§5 verification and refuses to ship on any failure. Parts the library
cannot resolve escalate with a complete package instead of receiving
geometry; one unresolvable part never blocks the resolvable ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shop_drawings.generation.config import DXF_INSUNITS, GenerationConfig
from shop_drawings.generation.cutlist import write_cut_list
from shop_drawings.generation.dxf import write_cut_sheets
from shop_drawings.generation.errors import FabricationError, UnresolvablePartError
from shop_drawings.generation.escalation import Escalation, write_escalation_package
from shop_drawings.generation.gate import ensure_generatable
from shop_drawings.generation.geometry import PartPlan, resolve_part
from shop_drawings.generation.model3dm import write_model
from shop_drawings.generation.verify import (
    CheckResult,
    check_constraint_closure,
    check_dxf_sheets,
    check_geometric_sanity,
    check_roundtrip,
    check_traceability,
)
from shop_drawings.schema import FurnitureSpec, Part, PartType


@dataclass(frozen=True)
class GenerationResult:
    """What one generation job produced, with its verification record."""

    model_path: Path | None
    sheet_paths: tuple[Path, ...]
    cut_list_path: Path | None
    escalations: tuple[Escalation, ...]
    checks: tuple[CheckResult, ...]


def generate(
    spec: FurnitureSpec,
    config: GenerationConfig | None = None,
    out_dir: Path | None = None,
) -> GenerationResult:
    """Generate all fabrication files for an approved spec.

    Raises :class:`NotApprovedError` before anything is written unless the
    spec passed the review gate, and :class:`FabricationError` after
    removing every written file if a §5 check fails. Parts outside the v1
    primitive library escalate; everything else generates normally.
    """
    config = config or GenerationConfig()
    out_path = out_dir or Path.cwd()
    out_path.mkdir(parents=True, exist_ok=True)

    # Gate — checked in the generator as well as the schema (blueprint §4).
    ensure_generatable(spec)

    plans: list[PartPlan] = []
    unresolvable: list[tuple[Part, list[str]]] = []
    for part in spec.parts:
        try:
            plans.append(resolve_part(part))
        except UnresolvablePartError as error:
            unresolvable.append((part, error.reasons))

    fabricable = [plan for plan in plans if plan.part_type is not PartType.HARDWARE]
    model_path = write_model(spec, fabricable, config, out_path) if fabricable else None
    sheet_paths = (
        write_cut_sheets(
            fabricable, config, DXF_INSUNITS[spec.project.units], out_path, spec.project.name
        )
        if fabricable
        else []
    )
    cut_list_path = write_cut_list(spec, plans, config, out_path)

    checks = [
        check_traceability(spec),
        check_geometric_sanity(spec, plans),
        check_constraint_closure(spec),
    ]
    if sheet_paths:
        checks.append(check_dxf_sheets(sheet_paths, config, spec.project.units))
    if model_path is not None:
        checks.append(check_roundtrip(spec, fabricable, config, model_path))

    failures = [f for check in checks for f in check.failures]
    if failures:
        _remove(model_path, *sheet_paths, cut_list_path)
        raise FabricationError(
            f"generation of '{spec.project.name}' failed verification — "
            f"{len(failures)} failure(s); all outputs were removed",
            failures=failures,
        )

    escalations = tuple(
        write_escalation_package(spec, part, reasons, out_path) for part, reasons in unresolvable
    )
    return GenerationResult(
        model_path=model_path,
        sheet_paths=tuple(sheet_paths),
        cut_list_path=cut_list_path,
        escalations=escalations,
        checks=tuple(checks),
    )


def _remove(*paths: Path | None) -> None:
    """Remove written outputs so nothing unsafe leaves the pipeline."""
    for path in paths:
        if path is not None and path.exists():
            path.unlink()
