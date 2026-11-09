"""End-to-end extraction: a design file in, a typed draft spec out.

This module orchestrates the stages and owns the one policy decision that
spans them — how vision callouts join the deterministic parse:

- Callouts below the review-confidence floor never enter the parse; they
  become unresolved questions immediately (a value the model itself doubts
  must be verified, not attached on the strength of a grammar match).
- A callout that re-reads a line the text layer already found upgrades that
  line: the block inherits the model's confidence cap and role, so recorded
  confidence is the minimum across readers.
- A callout for a line the text layer does not have (scans, sketches,
  photos) synthesizes a block at the model's region and flows through the
  same grammar and association as any text-layer line.

Vision ``part_label`` hints are deliberately *not* used to attach dimensions
in v1 — attachment stays deterministic (proximity + grammar) so every
association is explicable without reference to the model's judgment. The
golden-file evaluation (blueprint §5) will show whether model-assisted
attachment earns its place later.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from shop_drawings.schema import FurnitureSpec

from .associate import (
    REVIEW_CONFIDENCE_FLOOR,
    Assembly,
    assemble,
)
from .geometry import Region
from .ingest import ingest_design
from .models import DesignDocument, TextBlock, Unassociated, UnassociatedKind
from .reconcile import build_spec
from .vision import VisionCallout, VisionClient


@dataclass(frozen=True)
class ExtractionResult:
    """The draft spec plus everything it was built from."""

    spec: FurnitureSpec
    assembly: Assembly
    document: DesignDocument
    vision_callouts: tuple[VisionCallout, ...]


def extract_spec(
    path: Path,
    vision_client: VisionClient | None = None,
    project_name: str | None = None,
) -> ExtractionResult:
    """Extract a schema-valid draft spec from a design file.

    ``vision_client`` is optional: text-layer PDFs extract fully without one,
    and scans or sketches surface their readings as review questions until a
    client is configured. The returned spec is always ``needs_review`` —
    the gate to ``approved`` lives in the review flow, never here.
    """
    document = ingest_design(path)
    callouts: tuple[VisionCallout, ...] = ()
    rejected: list[Unassociated] = []
    if vision_client is not None:
        callouts = vision_client.extract(document)
        document, rejected = merge_vision_callouts(document, callouts)

    assembly = assemble(document)
    if rejected:
        assembly = replace(assembly, unassociated=assembly.unassociated + tuple(rejected))
    spec = build_spec(assembly, source_name=path.name)
    if project_name is not None:
        spec = spec.model_copy(
            update={"project": spec.project.model_copy(update={"name": project_name})}
        )
    # "Nothing read" means no parts, no constraints, no questions raised by
    # findings — title-block defaults alone don't count as content.
    if not (assembly.parts or assembly.constraints or assembly.unassociated):
        spec = _mark_unreadable(spec, path.name)
    return ExtractionResult(
        spec=spec, assembly=assembly, document=document, vision_callouts=callouts
    )


def merge_vision_callouts(
    document: DesignDocument,
    callouts: tuple[VisionCallout, ...],
) -> tuple[DesignDocument, list[Unassociated]]:
    """Fold callouts into the document; return the merged doc and rejections.

    Text-layer blocks win when both sources read the same line — their
    regions come from the PDF itself — but they inherit the model's
    confidence cap and role. Unmatched callouts become new blocks.
    """
    upgraded: dict[int, TextBlock] = {}
    synthesized: list[TextBlock] = []
    rejected: list[Unassociated] = []

    for callout in callouts:
        index = _find_text_layer_match(document, callout)
        if index is not None and index not in upgraded:
            original = document.blocks[index]
            upgraded[index] = replace(
                original,
                vision_confidence=callout.confidence,
                role=callout.role,
            )
            continue
        if callout.confidence < REVIEW_CONFIDENCE_FLOOR:
            rejected.append(
                Unassociated(
                    kind=UnassociatedKind.LOW_CONFIDENCE_DIMENSION,
                    detail=(
                        f"vision reading {callout.text!r} (role {callout.role}) reports "
                        f"confidence {callout.confidence:.2f}, below the "
                        f"{REVIEW_CONFIDENCE_FLOOR:.2f} floor — verify it against the page"
                    ),
                    block=_block_from(callout),
                )
            )
            continue
        synthesized.append(_block_from(callout))

    blocks = list(document.blocks)
    for index, block in upgraded.items():
        blocks[index] = block
    blocks.extend(synthesized)
    merged = DesignDocument(
        source_name=document.source_name,
        pages=document.pages,
        blocks=tuple(sorted(blocks, key=lambda b: (b.page, b.region.y0, b.region.x0))),
    )
    return merged, rejected


def _block_from(callout: VisionCallout) -> TextBlock:
    return TextBlock(
        page=callout.page,
        text=callout.text,
        region=Region(*callout.region),
        vision_confidence=callout.confidence,
        role=callout.role,
    )


def _find_text_layer_match(document: DesignDocument, callout: VisionCallout) -> int | None:
    """Index of the text-layer block the callout re-read, if any.

    Text must match exactly after whitespace normalization, and the callout's
    (padded) region must contain the block — vision boxes wobble, words do not.
    """
    wanted = " ".join(callout.text.upper().split())
    hunt_area = Region(*callout.region).padded(0.5)
    for index, block in enumerate(document.blocks):
        if block.page != callout.page:
            continue
        if " ".join(block.text.upper().split()) != wanted:
            continue
        if hunt_area.contains(block.region):
            return index
    return None


def _mark_unreadable(spec: FurnitureSpec, source_name: str) -> FurnitureSpec:
    """A spec with nothing in it is a question, not a silent empty file."""
    question = (
        f"No fabrication content could be read from {source_name}. Confirm this "
        "file is the design drawing, or enter its contents manually."
    )
    return spec.model_copy(update={"unresolved_questions": [*spec.unresolved_questions, question]})
