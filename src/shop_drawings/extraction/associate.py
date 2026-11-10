"""Association: parsed findings become part callouts — or review questions.

This is where the blueprint's medium-reliability task lives (§5: "associate a
dimension with the right part/feature"). The rule the whole module enforces:

- Findings inside a labeled callout column attach to that part directly.
- Tagged dimensions floating on the sheet attach to the *nearest* callout with
  a confidence that decays with distance — and below the review floor they do
  not attach at all; they become ``Unassociated`` and later a question.
- Bare numbers are never typed by geometry. A number with no stated role is a
  question, never a dimension (locked decision: no silent inference).

All functions are pure; the pipeline hands them a ``DesignDocument``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Final

from .geometry import Region
from .models import (
    BareNumber,
    DesignDocument,
    LineParse,
    ParsedConstraint,
    ParsedDimension,
    ParsedFeature,
    TextBlock,
    TitleKV,
    Unassociated,
    UnassociatedKind,
    VariantDimension,
)
from .parsing import apply_block_overrides, looks_like_data, parse_block

#: Attached findings at or above this confidence go into the spec; below it,
#: they become unresolved questions for the reviewer.
REVIEW_CONFIDENCE_FLOOR: Final = 0.6

#: Tagged dimensions farther than this (normalized page units) from every
#: callout are unassociated outright.
NEAR_RADIUS: Final = 0.45

#: Distance decay for nearby association: exp(-distance / scale).
NEAR_DECAY_SCALE: Final = 0.12

#: When the two nearest callouts are this close, association is ambiguous.
AMBIGUITY_MARGIN: Final = 0.02

#: Callout membership: horizontal alignment and vertical reach, in fractions
#: of the page. Schedule rows share a column; a label starts a new row.
_COLUMN_TOLERANCE: Final = 0.04
_ROW_REACH: Final = 0.22

_SLUG_RUN: Final = re.compile(r"[^a-z0-9]+")


def slugify(label: str) -> str:
    """``SIDE PANEL`` → ``side_panel`` — a schema-valid part id stem."""
    slug = _SLUG_RUN.sub("_", label.strip().lower()).strip("_")
    return slug or "part"


@dataclass(frozen=True)
class AttachedDimension:
    """A parsed dimension with association composed onto parse confidence."""

    dimension: ParsedDimension
    association: float

    @property
    def confidence(self) -> float:
        return self.dimension.confidence * self.association


@dataclass(frozen=True)
class AttachedFeature:
    """A joinery/machining callout attached to its part."""

    feature: ParsedFeature
    confidence: float


@dataclass(frozen=True)
class PartCallout:
    """Everything the sheet says about one part, in one place."""

    label: str
    part_id: str
    quantity: int
    quantity_stated: bool
    label_block: TextBlock
    material: str | None = None
    material_block: TextBlock | None = None
    material_confidence: float = 0.0
    material_thickness: float | None = None
    sizes: tuple[float, ...] = field(default=())
    sizes_block: TextBlock | None = None
    sizes_confidence: float = 0.0
    dimensions: tuple[AttachedDimension, ...] = field(default=())
    features: tuple[AttachedFeature, ...] = field(default=())
    edges: tuple[str, ...] = field(default=())
    member_blocks: tuple[TextBlock, ...] = field(default=())

    @property
    def region(self) -> Region:
        """Bounding region of every member block — the association target."""
        region = self.label_block.region
        for block in self.member_blocks:
            region = region.union(block.region)
        return region


@dataclass(frozen=True)
class Assembly:
    """Association output: callouts, constraints, title block, leftovers."""

    parts: tuple[PartCallout, ...]
    constraints: tuple[ParsedConstraint, ...]
    title_block: tuple[TitleKV, ...]
    unassociated: tuple[Unassociated, ...]


def parse_document(document: DesignDocument) -> list[LineParse]:
    """Parse every block, in stable reading order, with vision overrides applied."""
    blocks = sorted(document.blocks, key=lambda b: (b.page, b.region.y0, b.region.x0))
    return [apply_block_overrides(parse_block(block)) for block in blocks]


class _CalloutBuilder:
    """Accumulates member lines for one part callout."""

    def __init__(
        self, label: str, quantity: int, quantity_stated: bool, label_block: TextBlock
    ) -> None:
        self.label = label
        self.quantity = quantity
        self.quantity_stated = quantity_stated
        self.label_block = label_block
        self.material: str | None = None
        self.material_block: TextBlock | None = None
        self.material_confidence: float = 0.0
        self.material_thickness: float | None = None
        self.sizes: tuple[float, ...] = ()
        self.sizes_block: TextBlock | None = None
        self.sizes_confidence: float = 0.0
        self.dimensions: list[AttachedDimension] = []
        self.features: list[AttachedFeature] = []
        self.edges: list[str] = []
        self.member_blocks: list[TextBlock] = [label_block]

    @property
    def region(self) -> Region:
        """Bounding region of every member block — the association target."""
        region = self.label_block.region
        for block in self.member_blocks[1:]:
            region = region.union(block.region)
        return region

    def adopt(self, parsed: LineParse) -> None:
        """Fold a member line's findings in at full association confidence."""
        block = parsed.block
        self.member_blocks.append(block)
        if parsed.material is not None:
            self.material = parsed.material.material
            self.material_thickness = parsed.material.thickness
            self.material_block = block
            self.material_confidence = parsed.material.confidence
        if parsed.sizes is not None:
            self.sizes = parsed.sizes.values
            self.sizes_block = block
            self.sizes_confidence = parsed.sizes.confidence
        if parsed.edge_banding is not None:
            self.edges.extend(e.lower() for e in parsed.edge_banding.edges)
        if parsed.feature is not None:
            self.features.append(AttachedFeature(feature=parsed.feature, confidence=1.0))
        for dimension in parsed.dimensions:
            self.dimensions.append(AttachedDimension(dimension=dimension, association=1.0))

    def build(self, part_id: str) -> PartCallout:
        return PartCallout(
            label=self.label,
            part_id=part_id,
            quantity=self.quantity,
            quantity_stated=self.quantity_stated,
            label_block=self.label_block,
            material=self.material,
            material_block=self.material_block,
            material_confidence=self.material_confidence,
            material_thickness=self.material_thickness,
            sizes=self.sizes,
            sizes_block=self.sizes_block,
            sizes_confidence=self.sizes_confidence,
            dimensions=tuple(self.dimensions),
            features=tuple(self.features),
            edges=tuple(self.edges),
            member_blocks=tuple(self.member_blocks),
        )


def _belongs_to_callout(block: TextBlock, label_block: TextBlock) -> bool:
    """Same column, and within a few text lines below the label."""
    same_column = abs(block.region.x0 - label_block.region.x0) <= _COLUMN_TOLERANCE
    below = -0.01 <= block.region.y0 - label_block.region.y0 <= _ROW_REACH
    return same_column and below


def _bare_number_unassociated(bare: BareNumber) -> Unassociated:
    """A number with no stated role is a question, never a dimension."""
    return Unassociated(
        kind=UnassociatedKind.BARE_NUMBER,
        detail=(
            f"bare dimension {bare.value:g} in {bare.block.text!r} has no stated "
            "role or nearby part; assign its meaning manually"
        ),
        block=bare.block,
        best_confidence=bare.confidence,
    )


def _variant_unassociated(variant: VariantDimension) -> Unassociated:
    """A line stating alternatives routes to a question — never a pick."""
    stated = " or ".join(f"{v:g}" for v in variant.values)
    return Unassociated(
        kind=UnassociatedKind.VARIANT_DIMENSION,
        detail=(
            f"dimension callout {variant.block.text!r} offers the alternatives "
            f"({stated}); variant sizes are never auto-selected"
        ),
        block=variant.block,
        best_confidence=variant.confidence,
    )


def _association_for(
    dimension: ParsedDimension, callouts: list[_CalloutBuilder]
) -> tuple[float, str | None]:
    """Best (confidence, label) for a floating dimension; empty if unplaceable.

    Returns ``(association, label_or_none)``. Ambiguity (two callouts equally
    near) is reported as ``(0.0, "ambiguous")``.
    """
    ranked = sorted(
        (
            (dimension.block.region.center_distance(callout.region), callout)
            for callout in callouts
            if callout.label_block.page == dimension.block.page
        ),
        key=lambda pair: pair[0],
    )
    if not ranked or ranked[0][0] > NEAR_RADIUS:
        return 0.0, None
    best_distance, best = ranked[0]
    if len(ranked) > 1 and ranked[1][0] - best_distance < AMBIGUITY_MARGIN:
        return 0.0, "ambiguous"
    association = math.exp(-best_distance / NEAR_DECAY_SCALE)
    return association, best.label


def assemble(document: DesignDocument) -> Assembly:
    """Group parsed lines into part callouts and route the rest."""
    parses = parse_document(document)
    builders: list[_CalloutBuilder] = []
    constraints: list[ParsedConstraint] = []
    title_block: list[TitleKV] = []
    unassociated: list[Unassociated] = []
    used_ids: dict[str, int] = {}
    # Blocks a pass-1 callout adopted are done: pass 2 must not re-attach
    # them as floating findings (a column line would attach twice).
    consumed: set[int] = set()

    # Pass 1: callout columns.
    for parsed in parses:
        block = parsed.block
        if parsed.title_kv is not None:
            title_block.append(parsed.title_kv)
            continue
        if parsed.constraints:
            constraints.extend(parsed.constraints)
            continue
        if parsed.part_label is not None:
            label = parsed.part_label
            builders.append(
                _CalloutBuilder(label.label, label.quantity, label.quantity_stated, block)
            )
            continue
        if parsed.variant is not None:
            # Checked before column adoption: a variant in a part's own column
            # is still only a question — there is no value here to adopt.
            unassociated.append(_variant_unassociated(parsed.variant))
            continue
        current = builders[-1] if builders else None
        if current is not None and _belongs_to_callout(block, current.label_block):
            current.adopt(parsed)
            consumed.add(id(block))
            continue
        # Pass-2 candidates float free of any callout column.
        if parsed.material is not None or parsed.sizes is not None or parsed.feature is not None:
            unassociated.append(
                Unassociated(
                    kind=UnassociatedKind.UNPARSEABLE_ANNOTATION,
                    detail=f"orphaned callout line {block.text!r} belongs to no part column",
                    block=block,
                )
            )
            continue
        if not parsed.is_empty:
            continue  # floating dims and bare numbers are handled in pass 2
        if looks_like_data(block):
            unassociated.append(
                Unassociated(
                    kind=UnassociatedKind.UNPARSEABLE_ANNOTATION,
                    detail=f"could not interpret annotation {block.text!r}",
                    block=block,
                )
            )

    # Pass 2: floating tagged dimensions attach to the nearest callout.
    # Adopted lines attached at full confidence already — but an untyped
    # bare number on an adopted line still needs its question: no geometry
    # association gives it a role.
    for parsed in parses:
        if id(parsed.block) in consumed:
            if parsed.bare_number is not None:
                unassociated.append(_bare_number_unassociated(parsed.bare_number))
            continue
        if parsed.is_empty or parsed.title_kv or parsed.constraints or parsed.part_label:
            continue
        if parsed.variant is not None:
            continue  # handled in pass 1 as a variant question
        if parsed.material is not None or parsed.sizes is not None or parsed.feature is not None:
            continue  # handled in pass 1 as orphaned callout lines
        for dimension in parsed.dimensions:
            association, assoc_label = _association_for(dimension, builders)
            if assoc_label == "ambiguous":
                unassociated.append(
                    Unassociated(
                        kind=UnassociatedKind.AMBIGUOUS_ASSOCIATION,
                        detail=(
                            f"dimension {dimension.block.text!r} is equally near two "
                            "part callouts; assign it manually"
                        ),
                        block=dimension.block,
                    )
                )
                continue
            confidence = dimension.confidence * association
            if assoc_label is None:
                reason = (
                    "no part callout on its page to associate with"
                    if not builders
                    else "no part callout within association radius"
                )
                unassociated.append(
                    Unassociated(
                        kind=UnassociatedKind.LOW_CONFIDENCE_DIMENSION,
                        detail=f"dimension {dimension.block.text!r} has {reason}",
                        block=dimension.block,
                        best_confidence=confidence,
                    )
                )
                continue
            target = next(b for b in builders if b.label == assoc_label)
            if confidence < REVIEW_CONFIDENCE_FLOOR:
                unassociated.append(
                    Unassociated(
                        kind=UnassociatedKind.LOW_CONFIDENCE_DIMENSION,
                        detail=(
                            f"dimension {dimension.block.text!r} best-matches part "
                            f"{assoc_label!r} at confidence {confidence:.2f} (floor "
                            f"{REVIEW_CONFIDENCE_FLOOR})"
                        ),
                        block=dimension.block,
                        best_confidence=confidence,
                    )
                )
                continue
            target.dimensions.append(
                AttachedDimension(dimension=dimension, association=association)
            )
        if parsed.bare_number is not None:
            unassociated.append(_bare_number_unassociated(parsed.bare_number))

    parts: list[PartCallout] = []
    for builder in builders:
        count = used_ids.get(builder.label, 0) + 1
        used_ids[builder.label] = count
        part_id = slugify(builder.label) if count == 1 else f"{slugify(builder.label)}_{count}"
        parts.append(builder.build(part_id))

    return Assembly(
        parts=tuple(parts),
        constraints=tuple(constraints),
        title_block=tuple(title_block),
        unassociated=tuple(unassociated),
    )
