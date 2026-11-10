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
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Literal

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
        if index is not None:
            # A second model reading of the same line re-upgrades the block
            # (re-capping its confidence) rather than synthesizing a duplicate.
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

    The model may read a line in pieces the text layer groups differently —
    a fragment of a merged visual line ("FRONT ELEVATION (1:7.6)" inside
    "FRONT ELEVATION (1:7.6) SIDE ELEVATION (1:7.6)"), or the whole line
    with different dash/whitespace variants — so text matches when the
    normalized token sequences are equal or one is a contiguous run of the
    other. The region only has to agree that these are the same line: vision
    boxes wobble and drift (on live sheets by up to a quarter page toward
    the bottom, see _MATCH_REGION_TOLERANCE), so either box must cover the
    other's center or the centers must sit within the tolerance. Text
    quality ranks ahead of region fit: an exact reading beats a fragment,
    then the nearer center wins.
    """
    wanted = _match_tokens(callout.text)
    hunt = Region(*callout.region)
    best: tuple[tuple[int, float], int] | None = None
    for index, block in enumerate(document.blocks):
        if block.page != callout.page:
            continue
        have = _match_tokens(block.text)
        if wanted == have:
            tier = 0
        elif _tokens_contained(wanted, have) or _tokens_contained(have, wanted):
            tier = 1
        else:
            continue
        if not (
            hunt.contains(block.region)
            or block.region.contains(hunt)
            or hunt.center_distance(block.region) <= _MATCH_REGION_TOLERANCE
        ):
            continue
        rank = (tier, hunt.center_distance(block.region))
        if best is None or rank < best[0]:
            best = (rank, index)
    return None if best is None else best[1]


#: How far a callout's region may sit from a text-layer block's region and
#: still be "pointing at" that line, in normalized page units. The text match
#: is the discriminating signal; vision boxes merely locate the neighborhood.
#: Live job 20261109-185416-21ddac20 (Globus v4 parts schedule) showed the
#: model's y-coordinates drifting up to ~0.26 of page height on dense sheets
#: — far beyond any box-padding wobble, but the token text still matched
#: exactly. Page-sized or generous regions still match everything through
#: the covers-the-center check.
_MATCH_REGION_TOLERANCE: Final = 0.3


def _match_tokens(text: str) -> list[str]:
    """Case-folded whitespace tokens, dash variants unified, digits split
    from following letters so "21.75IN" matches "21.75 IN"."""
    folded = re.sub(r"[\u2212\u2013\u2014]", "-", text.upper())
    folded = re.sub(r"(?<=\d)(?=[A-Z])", " ", folded)
    return folded.split()


def _tokens_contained(needle: list[str], haystack: list[str]) -> bool:
    """True when ``needle`` appears in ``haystack`` as a contiguous run."""
    span = len(needle)
    return span > 0 and any(
        haystack[i : i + span] == needle for i in range(len(haystack) - span + 1)
    )


def _mark_unreadable(spec: FurnitureSpec, source_name: str) -> FurnitureSpec:
    """A spec with nothing in it is a question, not a silent empty file."""
    question = (
        f"No fabrication content could be read from {source_name}. Confirm this "
        "file is the design drawing, re-annotated in the uppercase callout style "
        "the parser reads (like 'SIDE PANEL — QTY 2' with tagged dimensions), "
        "or upload a different file."
    )
    return spec.model_copy(update={"unresolved_questions": [*spec.unresolved_questions, question]})
