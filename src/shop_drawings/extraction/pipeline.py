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

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

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

logger = logging.getLogger(__name__)

#: What became of one vision callout — the thought process the review UI shows.
#: ``upgraded``: the model re-read a line the text layer already had (its
#: confidence caps that block's findings). ``proposed``: the callout became a
#: new candidate block at or above the review floor. ``question``: below the
#: floor, so it never enters the parse — it becomes an unresolved question.
VisionOutcome = Literal["upgraded", "proposed", "question"]


@dataclass(frozen=True)
class VisionObservation:
    """One vision callout plus what the pipeline did with it.

    This is the review-facing record — the model's reading never enters the
    spec unrecorded; every callout shows up here with its disposition.
    """

    text: str
    page: int
    region: tuple[float, float, float, float]
    role: str | None
    part_label: str | None
    confidence: float
    outcome: VisionOutcome

    @classmethod
    def of(cls, callout: VisionCallout, outcome: VisionOutcome) -> VisionObservation:
        return cls(
            text=callout.text,
            page=callout.page,
            region=callout.region,
            role=callout.role,
            part_label=callout.part_label,
            confidence=callout.confidence,
            outcome=outcome,
        )


@dataclass(frozen=True)
class VisionRun:
    """The record of one vision pass, for the review UI.

    ``error`` is set only when the pass degraded to the text-only path —
    the review UI shows what failed instead of the extraction pretending
    vision never ran.
    """

    observations: tuple[VisionObservation, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class ExtractionResult:
    """The draft spec plus everything it was built from."""

    spec: FurnitureSpec
    assembly: Assembly
    document: DesignDocument
    vision_callouts: tuple[VisionCallout, ...]
    vision: VisionRun | None = None


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
    run: VisionRun | None = None
    if vision_client is not None:
        try:
            callouts = vision_client.extract(document)
        except Exception as exc:
            # Deliberate degradation, not a swallowed error: an API error,
            # timeout, or malformed reply must never fail the upload — the
            # text layer still extracts what it can. Logged with traceback;
            # the review UI shows the failure instead of pretending vision
            # never ran.
            logger.warning(
                "vision pass degraded to text-only extraction: %s: %s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            run = VisionRun(error=f"{type(exc).__name__}: {exc}")
        else:
            document, rejected, observations = merge_vision_callouts_reported(document, callouts)
            run = VisionRun(observations=observations)

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
        spec=spec,
        assembly=assembly,
        document=document,
        vision_callouts=callouts,
        vision=run,
    )


def vision_report(result: ExtractionResult) -> dict[str, object]:
    """The review UI's JSON record of the vision pass — or its absence.

    Written per job next to the spec so the review gate can show the
    model's thought process without re-running anything.
    """
    run = result.vision
    if run is None:
        return {"enabled": False, "error": None, "observations": []}
    return {
        "enabled": True,
        "error": run.error,
        "observations": [
            {
                "text": o.text,
                "page": o.page,
                "region": list(o.region),
                "role": o.role,
                "part_label": o.part_label,
                "confidence": o.confidence,
                "outcome": o.outcome,
            }
            for o in run.observations
        ],
    }


def merge_vision_callouts(
    document: DesignDocument,
    callouts: tuple[VisionCallout, ...],
) -> tuple[DesignDocument, list[Unassociated]]:
    """Fold callouts into the document; return the merged doc and rejections."""
    merged, rejected, _ = merge_vision_callouts_reported(document, callouts)
    return merged, rejected


def merge_vision_callouts_reported(
    document: DesignDocument,
    callouts: tuple[VisionCallout, ...],
) -> tuple[DesignDocument, list[Unassociated], tuple[VisionObservation, ...]]:
    """Fold callouts into the document, reporting each one's disposition.

    Text-layer blocks win when both sources read the same line — their
    regions come from the PDF itself — but they inherit the model's
    confidence cap and role. Unmatched callouts become new blocks (at or
    above the review floor) or unresolved questions (below it).
    """
    upgraded: dict[int, TextBlock] = {}
    synthesized: list[TextBlock] = []
    rejected: list[Unassociated] = []
    observations: list[VisionObservation] = []

    for callout in callouts:
        index = _find_text_layer_match(document, callout)
        if index is not None and index not in upgraded:
            original = document.blocks[index]
            upgraded[index] = replace(
                original,
                vision_confidence=callout.confidence,
                role=callout.role,
            )
            observations.append(VisionObservation.of(callout, "upgraded"))
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
            observations.append(VisionObservation.of(callout, "question"))
            continue
        synthesized.append(_block_from(callout))
        observations.append(VisionObservation.of(callout, "proposed"))

    blocks = list(document.blocks)
    for index, block in upgraded.items():
        blocks[index] = block
    blocks.extend(synthesized)
    merged = DesignDocument(
        source_name=document.source_name,
        pages=document.pages,
        blocks=tuple(sorted(blocks, key=lambda b: (b.page, b.region.y0, b.region.x0))),
    )
    return merged, rejected, tuple(observations)


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
