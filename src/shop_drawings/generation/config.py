"""Studio conventions for generation — configuration, never hard-coded.

The blueprint locks this principle (§3, §6): layer names, object naming,
sheet stock, and units are studio decisions that change per shop, so they
live in a :class:`GenerationConfig` that the caller supplies. Every value
is in the *same units as the spec being generated*; nothing converts or
assumes behind the caller's back.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from shop_drawings.schema import Units

#: DXF $INSUNITS header values per the DXF reference (units one-byte code).
DXF_INSUNITS: dict[Units, int] = {
    Units.MM: 4,
    Units.CM: 5,
    Units.INCH: 1,
}


class LayerConfig(BaseModel):
    """Layer names and colors for the generated files.

    ``cut``/``pocket``/``drill`` follow the blueprint's fabrication-file
    convention (§5, citing R1 §2). ``part`` carries the part solids in the
    .3dm; ``sheet`` carries the sheet boundary and part labels on cut
    sheets, kept on its own layer so CAM can ignore it.

    Colors are DXF/rhino palette ints or ``(r, g, b, a)`` tuples.
    """

    model_config = ConfigDict(frozen=True)

    cut: str = "CUT"
    pocket: str = "POCKET"
    drill: str = "DRILL"
    part: str = "PART"
    sheet: str = "SHEET"

    cut_color: tuple[int, int, int, int] = (255, 0, 0, 255)
    pocket_color: tuple[int, int, int, int] = (0, 0, 255, 255)
    drill_color: tuple[int, int, int, int] = (0, 160, 0, 255)
    sheet_color: tuple[int, int, int, int] = (128, 128, 128, 255)

    def fabrication_layers(self) -> tuple[tuple[str, tuple[int, int, int, int]], ...]:
        """The layers every generated DXF must declare, in order."""
        return (
            (self.cut, self.cut_color),
            (self.pocket, self.pocket_color),
            (self.drill, self.drill_color),
        )


class NamingConfig(BaseModel):
    """Object and file naming patterns.

    Patterns are ``str.format`` templates. ``{part_id}`` is the spec part
    id (already validated by the schema, so it is safe in file names),
    ``{project}`` the project name, ``{ext}`` the file extension,
    ``{feature_type}`` the machining kind (drill/pocket), and
    ``{index:03d}``-style specs format per-part feature counters.
    Sheet files number sheets from 1, the shop-drawing convention.
    """

    model_config = ConfigDict(frozen=True)

    part_pattern: str = "{part_id}"
    feature_pattern: str = "{part_id}_{feature_type}_{index:03d}"
    model_file_pattern: str = "{project}.{ext}"
    sheet_file_pattern: str = "{project}_sheet-{index:02d}.{ext}"
    cutlist_file_pattern: str = "{project}_cutlist.{ext}"


class SheetStock(BaseModel):
    """One stocked sheet size, in spec units.

    ``length`` is the long edge, ``width`` the short edge; nesting may use
    either orientation.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    length: float = Field(gt=0, description="long edge, in spec units")
    width: float = Field(gt=0, description="short edge, in spec units")


#: Common plywood sheet sizes (mm) used unless the studio configures others.
DEFAULT_SHEETS: tuple[SheetStock, ...] = (
    SheetStock(name="plywood-2440x1220", length=2440, width=1220),
    SheetStock(name="plywood-3050x1220", length=3050, width=1220),
)


class GenerationConfig(BaseModel):
    """Every studio convention the generator consults, in one object."""

    model_config = ConfigDict(frozen=True)

    layers: LayerConfig = Field(default_factory=LayerConfig)
    naming: NamingConfig = Field(default_factory=NamingConfig)
    sheets: tuple[SheetStock, ...] = DEFAULT_SHEETS
    sheet_margin: float = Field(
        default=10.0, ge=0, description="keep-out from the sheet edge, in spec units"
    )
    part_gap: float = Field(
        default=20.0, gt=0, description="minimum distance between parts on a sheet"
    )
    label_height: float = Field(
        default=20.0, gt=0, description="part-label text height on cut sheets"
    )
    file3dm_version: int = Field(
        default=8, ge=8, le=8, description=".3dm archive version to write (v1: 8)"
    )
