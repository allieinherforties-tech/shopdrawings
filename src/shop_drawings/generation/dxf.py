"""The DXF writer — 2D cut sheets from approved specs.

Writes one DXF per stocked sheet with ``ezdxf``: closed part outlines on
the configured CUT layer, drill circles on DRILL, pocket rectangles on
POCKET, and a sheet boundary with part labels on the auxiliary SHEET
layer. The unit code follows the spec's declared units (mm→4, cm→5,
in→1); every layout value comes from the
:class:`~shop_drawings.generation.config.GenerationConfig`.

The sheet-stock check (blueprint §5) runs during layout: a part whose
outline fits no configured sheet — either orientation, inside the
configured margin — fails the job with both sizes named.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ezdxf.colors import RGB
from ezdxf.document import Drawing
from ezdxf.filemanagement import new
from ezdxf.layouts.base import BaseLayout

from shop_drawings.generation.config import GenerationConfig, SheetStock
from shop_drawings.generation.errors import FabricationError
from shop_drawings.generation.geometry import PartPlan
from shop_drawings.schema import PartType

#: DXF version written. R2018 is the current default CAD interchange format.
DXF_VERSION = "R2018"

_Rotation = tuple[float, float]  # (length along sheet X, width along sheet Y)


@dataclass(frozen=True)
class PlacedPart:
    """A part at its cut-sheet position (lower-left corner of its outline)."""

    plan: PartPlan
    x: float
    y: float


@dataclass(frozen=True)
class SheetLayout:
    """One sheet's worth of parts, plus the stock they are cut from."""

    stock: SheetStock
    placed: tuple[PlacedPart, ...]


def _usable(stock: SheetStock, config: GenerationConfig) -> _Rotation:
    """Sheet dimensions inside the configured keep-out margin."""
    return (
        stock.length - 2 * config.sheet_margin,
        stock.width - 2 * config.sheet_margin,
    )


def _oriented(plan: PartPlan, usable: _Rotation) -> _Rotation:
    """The part's outline in sheet axes, unrotated if it fits, else rotated.

    Raises :class:`FabricationError` when neither orientation fits — the
    blueprint's stock check (§5), named with both sizes.
    """
    if plan.length <= usable[0] and plan.width <= usable[1]:
        return (plan.length, plan.width)
    if plan.width <= usable[0] and plan.length <= usable[1]:
        return (plan.width, plan.length)
    raise FabricationError(
        f"part '{plan.part_id}' ({plan.length:g}x{plan.width:g}) fits no configured "
        f"sheet stock — largest usable area {usable[0]:g}x{usable[1]:g}"
    )


def layout_plans(plans: list[PartPlan], config: GenerationConfig) -> list[SheetLayout]:
    """Pack part outlines onto sheets: rows along +X, wrapping to +Y rows.

    Deterministic shelf packing in spec order — optimization nesting is
    nesting software's job, not v1's. All parts are assumed to share the
    stock of the first plan that fits them; parts needing different stock
    start a new sheet.
    """
    sheets: list[SheetLayout] = []
    placed: list[PlacedPart] = []
    stock: SheetStock | None = None
    cursor_x = 0.0
    cursor_y = 0.0
    row_depth = 0.0

    def flush() -> None:
        nonlocal stock, placed, cursor_x, cursor_y, row_depth
        if stock is not None and placed:
            sheets.append(SheetLayout(stock=stock, placed=tuple(placed)))
        stock = None
        placed = []
        cursor_x = cursor_y = row_depth = 0.0

    for plan in plans:
        if plan.part_type is PartType.HARDWARE:
            continue  # not fabricated; cut list only
        part_stock = next((s for s in config.sheets if plan.fits_inside(*_usable(s, config))), None)
        if part_stock is None:
            raise FabricationError(
                f"part '{plan.part_id}' ({plan.length:g}x{plan.width:g}) fits no configured "
                f"sheet stock inside a {config.sheet_margin:g} margin — configured stock: "
                + ", ".join(f"{s.name} ({s.length:g}x{s.width:g})" for s in config.sheets)
            )
        usable = _usable(part_stock, config)
        length, width = _oriented(plan, usable)

        if stock is not None and part_stock is not stock:
            flush()  # different stock starts a new sheet
        elif stock is not None and cursor_x + length > usable[0]:
            if cursor_y + row_depth + config.part_gap + width <= usable[1]:
                cursor_y += row_depth + config.part_gap  # next row, same sheet
                cursor_x = 0.0
                row_depth = 0.0
            else:
                flush()  # sheet is full
        stock = part_stock
        placed.append(PlacedPart(plan=plan, x=cursor_x, y=cursor_y))
        cursor_x += length + config.part_gap
        row_depth = max(row_depth, width)
    flush()
    return sheets


def _add_outline(
    msp: BaseLayout, placed: PlacedPart, layer: str, origin: tuple[float, float]
) -> None:
    """Closed rectangular outline for one placed part."""
    x0, y0 = origin[0] + placed.x, origin[1] + placed.y
    x1, y1 = x0 + placed.plan.length, y0 + placed.plan.width
    msp.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        format="xy",
        close=True,
        dxfattribs={"layer": layer},
    )


def _add_true_color_layer(doc: Drawing, name: str, rgb: tuple[int, int, int, int]) -> None:
    """Declare a layer with its configured true color (RGB tuples for both formats)."""
    doc.layers.add(name)
    doc.layers.get(name).rgb = RGB(rgb[0], rgb[1], rgb[2])


def write_cut_sheets(
    plans: list[PartPlan], config: GenerationConfig, units_code: int, out_dir: Path, stem: str
) -> list[Path]:
    """Write one DXF per sheet; returns the paths written."""
    sheets = layout_plans(plans, config)
    paths: list[Path] = []
    for index, sheet in enumerate(sheets):
        doc = new(DXF_VERSION, setup=True)
        doc.header["$INSUNITS"] = units_code
        doc.units = units_code
        for name, color in config.layers.fabrication_layers():
            _add_true_color_layer(doc, name, color)
        _add_true_color_layer(doc, config.layers.sheet, config.layers.sheet_color)
        msp = doc.modelspace()

        sheet_length, sheet_width = sheet.stock.length, sheet.stock.width
        msp.add_lwpolyline(
            [(0, 0), (sheet_length, 0), (sheet_length, sheet_width), (0, sheet_width)],
            format="xy",
            close=True,
            dxfattribs={"layer": config.layers.sheet},
        )
        # Parts are drawn inside the keep-out margin; the boundary is the frame.
        origin = (config.sheet_margin, config.sheet_margin)
        for placed in sheet.placed:
            _add_outline(msp, placed, config.layers.cut, origin)
            for drill in placed.plan.drills:
                msp.add_circle(
                    (
                        origin[0] + placed.x + drill.center_x,
                        origin[1] + placed.y + drill.center_y,
                    ),
                    radius=drill.diameter / 2,
                    dxfattribs={"layer": config.layers.drill},
                )
            for pocket in placed.plan.pockets:
                px0, py0 = origin[0] + placed.x + pocket.x, origin[1] + placed.y + pocket.y
                msp.add_lwpolyline(
                    [
                        (px0, py0),
                        (px0 + pocket.length, py0),
                        (px0 + pocket.length, py0 + pocket.width),
                        (px0, py0 + pocket.width),
                    ],
                    format="xy",
                    close=True,
                    dxfattribs={"layer": config.layers.pocket},
                )
            label = msp.add_text(
                placed.plan.part_id,
                dxfattribs={"layer": config.layers.sheet, "height": config.label_height},
            )
            label.set_placement((origin[0] + placed.x, origin[1] + placed.y - config.label_height))

        path = out_dir / config.naming.sheet_file_pattern.format(
            project=stem, index=index + 1, ext="dxf"
        )
        doc.saveas(path)
        paths.append(path)
    return paths
