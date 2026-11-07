"""The typed furniture specification schema — the center of the pipeline.

Blueprint §3: AI extraction produces a spec, a human approves it at the
review gate, and deterministic code generates every fabrication file from
the approved spec. The schema enforces the invariants the gate depends on,
in the model rather than by convention:

- **Traceability** — every dimension and constraint carries a source
  reference (page + region + extracted text). An untraceable value cannot
  be constructed.
- **Typed meaning** — values are classified by what they mean (overall vs
  clear vs hole diameter vs setback, ...). Unknown kinds and types are
  parse errors, so a typo can never silently become geometry.
- **The review gate** — a spec marked ``approved`` while questions are
  open or values are unreviewed fails validation.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Version of the schema itself, bumped on breaking changes so downstream
#: consumers (validation, generation, quoting) can reject specs they cannot trust.
SchemaVersion = Literal[1]
SCHEMA_VERSION: SchemaVersion = 1

_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"


class Units(StrEnum):
    """Length unit for every value in a spec. Millimeters unless proven otherwise."""

    MM = "mm"
    CM = "cm"
    INCH = "in"


class ReviewStatus(StrEnum):
    """Review state of a value, a part, or a whole spec."""

    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


class Edge(StrEnum):
    """Named edges of a part, for edge treatment such as banding."""

    FRONT = "front"
    BACK = "back"
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


class DimensionKind(StrEnum):
    """What a dimension *means* — never a bare number.

    Extent kinds describe a part's own size; role kinds classify a value by
    its job in the construction. The generator switches on these; an unknown
    kind is a validation error.
    """

    # Part extents.
    LENGTH = "length"
    WIDTH = "width"
    THICKNESS = "thickness"
    DEPTH = "depth"
    HEIGHT = "height"
    # Semantic roles.
    OVERALL = "overall"  # whole-assembly extent
    CLEAR = "clear"  # inside-face to inside-face
    HOLE_DIAMETER = "hole_diameter"
    SETBACK = "setback"  # offset from a reference face or edge
    SPACING = "spacing"  # on-center spacing of a repeating pattern
    EDGE_DISTANCE = "edge_distance"
    REVEAL = "reveal"
    OVERHANG = "overhang"


class ConstraintType(StrEnum):
    """Assembly-level dimensions the parts must sum to within tolerance."""

    OVERALL_WIDTH = "overall_width"
    OVERALL_HEIGHT = "overall_height"
    OVERALL_DEPTH = "overall_depth"
    CLEAR_WIDTH = "clear_width"
    CLEAR_HEIGHT = "clear_height"
    CLEAR_DEPTH = "clear_depth"


class FeatureType(StrEnum):
    """Features the v1 panel-and-case primitive library understands."""

    HOLE = "hole"
    SHELF_PIN_HOLES = "shelf_pin_holes"
    POCKET_HOLE = "pocket_hole"
    REBATE = "rebate"
    DADO = "dado"
    GROOVE = "groove"
    MORTISE = "mortise"
    TENON = "tenon"


class PartType(StrEnum):
    """Construction kind of a part. Grows with the primitive library."""

    PANEL = "panel"
    SOLID = "solid"
    HARDWARE = "hardware"


class SpecModel(BaseModel):
    """Base for all spec models: unknown fields are typos, so reject them."""

    model_config = ConfigDict(extra="forbid")


class SourceReference(SpecModel):
    """Where a value came from on the source design.

    ``region`` is a normalized ``(x0, y0, x1, y1)`` crop on the page, so any
    value — even an approved one — traces back to the exact pixels it was
    read from when the shop floor disputes it.
    """

    page: int = Field(ge=1, description="1-based page in the source design")
    region: tuple[float, float, float, float]
    text: str = Field(min_length=1, description="text the value was read from")

    @field_validator("region")
    @classmethod
    def _region_is_a_crop(
        cls, region: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = region
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError(
                "region must be a normalized crop (x0, y0, x1, y1) with "
                "0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1"
            )
        return region


class TracedValue(SpecModel):
    """A measured value with provenance, confidence, and review state."""

    value: float = Field(ge=0, description="magnitude in the project's units")
    tolerance: float = Field(default=0.0, ge=0, description="permitted deviation, same units")
    source: SourceReference
    confidence: float = Field(ge=0, le=1, description="extraction confidence, 0 to 1")
    status: ReviewStatus = ReviewStatus.NEEDS_REVIEW


class Dimension(TracedValue):
    """A single dimension value; the key naming it carries its kind."""

    reference: str | None = Field(
        default=None,
        description="named face or edge the value is measured from (setbacks, edge distances)",
    )


class Constraint(TracedValue):
    """An assembly-level dimension the parts must satisfy."""

    type: ConstraintType


class Feature(SpecModel):
    """A machined or joined feature on a part, sized by typed dimensions."""

    type: FeatureType
    dimensions: dict[DimensionKind, Dimension] = Field(default_factory=dict)
    note: str | None = Field(default=None, description="joinery or machining note")


class Part(SpecModel):
    """One fabricable part: material, typed dimensions, features, review state."""

    id: str = Field(
        pattern=_ID_PATTERN, description="stable id; becomes the object name in generated files"
    )
    type: PartType
    quantity: int = Field(default=1, ge=1)
    material: str = Field(min_length=1)
    dimensions: dict[DimensionKind, Dimension] = Field(default_factory=dict)
    edge_banding: list[Edge] = Field(default_factory=list)
    features: list[Feature] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(
        default_factory=list, description="context crops beyond per-value references"
    )
    status: ReviewStatus = ReviewStatus.NEEDS_REVIEW


class Project(SpecModel):
    """The job a spec describes and the design it was extracted from."""

    name: str = Field(min_length=1)
    units: Units = Units.MM
    source: str = Field(
        min_length=1, description="file name of the source design (PDF) this spec came from"
    )


def _dimension_errors(where: str, dimensions: dict[DimensionKind, Dimension]) -> list[str]:
    """Unapproved dimensions under one part or feature, as gate error strings."""
    return [
        f"'{where}' dimension '{kind.value}' is {dim.status.value}, not approved"
        for kind, dim in dimensions.items()
        if dim.status is not ReviewStatus.APPROVED
    ]


class FurnitureSpec(SpecModel):
    """The typed, source-traceable specification — the pipeline's single source of truth.

    Every output (.3dm, DXF cut sheets, cut lists) is generated from an
    approved instance; nothing else is authoritative.
    """

    schema_version: SchemaVersion = SCHEMA_VERSION
    project: Project
    parts: list[Part] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    status: ReviewStatus = ReviewStatus.NEEDS_REVIEW

    def approval_errors(self) -> list[str]:
        """Everything blocking this spec from being approved and generated.

        Empty means the review gate passes. The model validator refuses to
        construct an ``approved`` spec while this list is non-empty, and the
        generator refuses anything that has not been approved.
        """
        errors: list[str] = []
        if self.unresolved_questions:
            errors.append(
                f"{len(self.unresolved_questions)} unresolved question(s) must be resolved first"
            )
        for part in self.parts:
            if part.status is not ReviewStatus.APPROVED:
                errors.append(f"part '{part.id}' is {part.status.value}, not approved")
            errors.extend(_dimension_errors(part.id, part.dimensions))
            for feature in part.features:
                errors.extend(
                    _dimension_errors(f"{part.id}:{feature.type.value}", feature.dimensions)
                )
        for constraint in self.constraints:
            if constraint.status is not ReviewStatus.APPROVED:
                kind = constraint.type.value
                errors.append(f"constraint '{kind}' is {constraint.status.value}, not approved")
        return errors

    @model_validator(mode="after")
    def _enforce_review_gate(self) -> FurnitureSpec:
        if self.status is ReviewStatus.APPROVED:
            errors = self.approval_errors()
            if errors:
                raise ValueError(
                    "spec is marked approved but fails the review gate: " + "; ".join(errors)
                )
        return self
