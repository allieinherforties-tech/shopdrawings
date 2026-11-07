"""Intermediate extraction types — everything between raw page text and the spec.

These models are deliberately schema-free: parsing and association speak in
blocks and findings; only ``reconcile`` converts them into the typed
``FurnitureSpec``. Keeping the boundary here means the parser never needs to
know about the gate and the schema never needs to know about PDF coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from .geometry import Region


class UnassociatedKind(StrEnum):
    """Why a finding could not be placed with confidence."""

    LOW_CONFIDENCE_DIMENSION = "low_confidence_dimension"
    BARE_NUMBER = "bare_number"
    UNPARSEABLE_ANNOTATION = "unparseable_annotation"
    AMBIGUOUS_ASSOCIATION = "ambiguous_association"


#: Roles a vision callout may carry. Dimension roles match schema
#: ``DimensionKind`` values, constraint roles match ``ConstraintType`` values;
#: the rest map to parser finding types. Shared here so the parser, the
#: vision client, and the prompt all speak one vocabulary.
DIMENSION_ROLES: Final = (
    "width",
    "height",
    "depth",
    "length",
    "thickness",
    "hole_diameter",
    "spacing",
    "setback",
    "edge_distance",
    "reveal",
    "overhang",
    "clear",
    "overall",
)
CONSTRAINT_ROLES: Final = (
    "overall_width",
    "overall_height",
    "overall_depth",
    "clear_width",
    "clear_height",
    "clear_depth",
)
OTHER_ROLES: Final = ("part_label", "material", "edge_banding", "feature", "title")
KNOWN_ROLES: Final = DIMENSION_ROLES + CONSTRAINT_ROLES + OTHER_ROLES


@dataclass(frozen=True)
class TextBlock:
    """One visual line of text from a page, with where it sits.

    ``vision_confidence`` and ``role`` are set only when a vision model vouched
    for this block (or synthesized it from an image); the parser then caps
    confidence at the model's value and, for bare numbers, lets the role type
    the value. A text-layer-only block carries neither.
    """

    page: int
    text: str
    region: Region
    vision_confidence: float | None = None
    role: str | None = None


@dataclass(frozen=True)
class PageImage:
    """A rendered page: 1-based page number, image bytes, and media type."""

    page: int
    png: bytes
    media_type: str = "image/png"


@dataclass(frozen=True)
class DesignDocument:
    """What the pipeline reads: per-page text blocks and rendered images.

    PDF inputs carry text blocks (when the PDF has a text layer) plus renders.
    Sketch and photo inputs arrive as a single image page — same vision path,
    no text layer. That asymmetry is the whole input model.
    """

    source_name: str
    pages: tuple[PageImage, ...]
    blocks: tuple[TextBlock, ...] = field(default=())


@dataclass(frozen=True)
class ParsedDimension:
    """A typed dimension candidate, not yet attached to a part.

    ``confidence`` is parse confidence only; association confidence is
    composed onto it in ``associate``.
    """

    kind: str  # DimensionKind value; kept as str so parsing stays schema-free
    value: float
    block: TextBlock
    confidence: float
    reference: str | None = None


@dataclass(frozen=True)
class SizeSpan:
    """An ``a x b [x c]`` size line, kept ordered; kinds are decided on attach."""

    values: tuple[float, ...]
    block: TextBlock
    confidence: float


@dataclass(frozen=True)
class ParsedFeature:
    """A joinery or machining callout, sized by parsed dimensions when given."""

    type: str  # FeatureType value; parsing stays schema-free
    note: str | None
    dimensions: tuple[ParsedDimension, ...]
    block: TextBlock


@dataclass(frozen=True)
class ParsedConstraint:
    """An assembly-level dimension candidate (overall extents)."""

    type: str  # ConstraintType value
    value: float
    block: TextBlock
    confidence: float


@dataclass(frozen=True)
class PartLabel:
    """A candidate part label line, with its declared quantity when present."""

    label: str
    quantity: int
    block: TextBlock
    confidence: float


@dataclass(frozen=True)
class MaterialLine:
    """A stock line: ``18mm birch plywood`` — a material name and its thickness."""

    material: str
    thickness: float | None
    block: TextBlock
    confidence: float


@dataclass(frozen=True)
class TitleKV:
    """One title-block key/value pair, e.g. ``UNITS: MM``."""

    key: str
    value: str
    block: TextBlock
    confidence: float


@dataclass(frozen=True)
class BareNumber:
    """A bare number with no stated role — never typed by guesswork."""

    value: float
    block: TextBlock
    confidence: float


@dataclass(frozen=True)
class EdgeBanding:
    """An edge-treatment callout: ``EDGE BAND FRONT, TOP``."""

    edges: tuple[str, ...]  # Edge values
    block: TextBlock
    confidence: float


@dataclass(frozen=True)
class UnknownAnnotation:
    """A line that matched no pattern — recorded, never silently dropped."""

    text: str
    block: TextBlock


@dataclass(frozen=True)
class LineParse:
    """The full parse of one text block. Exactly one line in, all findings out.

    A single line can carry several findings (``Ø5 PINS @ 32, 37 FROM FRONT``
    is a hole pattern *and* a setback). ``sizes`` holds an ordered span whose
    kinds are only decided when a material line is present to corroborate.
    """

    block: TextBlock
    title_kv: TitleKV | None = None
    part_label: PartLabel | None = None
    material: MaterialLine | None = None
    sizes: SizeSpan | None = None
    constraints: tuple[ParsedConstraint, ...] = field(default=())
    dimensions: tuple[ParsedDimension, ...] = field(default=())
    feature: ParsedFeature | None = None
    edge_banding: EdgeBanding | None = None
    bare_number: BareNumber | None = None

    @property
    def is_empty(self) -> bool:
        """True when nothing matched — the caller decides what that means."""
        return (
            self.title_kv is None
            and self.part_label is None
            and self.material is None
            and self.sizes is None
            and not self.constraints
            and not self.dimensions
            and self.feature is None
            and self.edge_banding is None
            and self.bare_number is None
        )


@dataclass(frozen=True)
class Unassociated:
    """A finding the pipeline could not place — it becomes a review question."""

    kind: UnassociatedKind
    detail: str
    block: TextBlock
    best_confidence: float | None = None
