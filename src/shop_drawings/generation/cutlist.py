"""The cut list export — CSV rows derived from the approved spec.

The cut list is the first downstream consumer of the spec (blueprint §6:
designed so quoting and BOM build on it without rework), so it adds no
information of its own: every cell is a spec value formatted, not
recomputed. Rows are QUOTE_ALL CSV with CRLF line terminators.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from shop_drawings.generation.config import GenerationConfig
from shop_drawings.generation.geometry import PartPlan, extents_in_order, summarize_features
from shop_drawings.schema import FurnitureSpec, PartType

#: Column order is stable — downstream consumers parse positionally by name.
COLUMNS: tuple[str, ...] = (
    "part_id",
    "type",
    "quantity",
    "material",
    "length",
    "width",
    "thickness",
    "edge_banding",
    "features",
    "source_page",
)


@dataclass(frozen=True)
class CutListRow:
    """One cut-list line: a part and its machined summary."""

    part_id: str
    part_type: str
    quantity: int
    material: str
    length: str
    width: str
    thickness: str
    edge_banding: str
    features: str
    source_page: str


def fmt_dimension(value: float) -> str:
    """Spec units as entered — no trailing ``.0`` on whole numbers."""
    return f"{value:g}"


def build_rows(spec: FurnitureSpec, plans: list[PartPlan]) -> list[CutListRow]:
    """One row per spec part, in spec order — escalated parts included.

    A part the primitive library resolved is rowed from its plan. An
    escalated part has no resolved geometry, so its row carries the
    dimensions the spec *declares* (in extent order, no normalization)
    and a marker pointing at its escalation package — material ordering
    must not silently drop a part the library could not build.
    """
    plans_by_id = {plan.part_id: plan for plan in plans}
    rows: list[CutListRow] = []
    for part in spec.parts:
        plan = plans_by_id.get(part.id)
        source_page = str(part.source_references[0].page) if part.source_references else ""
        if plan is None:
            values = [dim.value for _, dim in extents_in_order(part)]
            rows.append(
                CutListRow(
                    part_id=part.id,
                    part_type=part.type.value,
                    quantity=part.quantity,
                    material=part.material,
                    length=fmt_dimension(values[0]) if len(values) > 0 else "",
                    width=fmt_dimension(values[1]) if len(values) > 1 else "",
                    thickness=fmt_dimension(values[2]) if len(values) > 2 else "",
                    edge_banding=" ".join(edge.value for edge in part.edge_banding),
                    features=f"escalated — see escalation/{part.id}/package.json",
                    source_page=source_page,
                )
            )
            continue
        is_hardware = plan.part_type is PartType.HARDWARE
        rows.append(
            CutListRow(
                part_id=plan.part_id,
                part_type=plan.part_type.value,
                quantity=plan.quantity,
                material=plan.material,
                length="" if is_hardware else fmt_dimension(plan.length),
                width="" if is_hardware else fmt_dimension(plan.width),
                thickness="" if is_hardware else fmt_dimension(plan.thickness),
                edge_banding=" ".join(edge.value for edge in plan.edge_banding),
                features="" if is_hardware else summarize_features(plan.drills, plan.pockets),
                source_page=source_page,
            )
        )
    return rows


def render_csv(rows: list[CutListRow]) -> str:
    """Render rows as QUOTE_ALL, CRLF-terminated CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow(
            (
                row.part_id,
                row.part_type,
                row.quantity,
                row.material,
                row.length,
                row.width,
                row.thickness,
                row.edge_banding,
                row.features,
                row.source_page,
            )
        )
    return buffer.getvalue()


def write_cut_list(
    spec: FurnitureSpec, plans: list[PartPlan], config: GenerationConfig, out_dir: Path
) -> Path:
    """Write the cut list CSV; returns the file path."""
    path = out_dir / config.naming.cutlist_file_pattern.format(project=spec.project.name, ext="csv")
    path.write_text(render_csv(build_rows(spec, plans)), encoding="utf-8", newline="")
    return path
