"""Extraction: design files in, typed draft specs out.

Public surface (the deterministic core needs no network):

- ``extract_spec`` — one call, PDF/image in, ``FurnitureSpec`` draft out.
- ``ingest_design`` — PDF/image to pages and text blocks.
- ``parse_block`` — the deterministic line grammar.
- ``assemble`` / ``build_spec`` — association and reconciliation.
- ``VisionClient`` / ``AnthropicVisionClient`` — the vision path, optional.
"""

from .associate import (
    REVIEW_CONFIDENCE_FLOOR,
    Assembly,
    AttachedDimension,
    PartCallout,
    assemble,
)
from .geometry import Region
from .ingest import ingest_design
from .models import (
    DesignDocument,
    PageImage,
    TextBlock,
    Unassociated,
    UnassociatedKind,
)
from .parsing import looks_like_data, parse_block
from .pipeline import ExtractionResult, extract_spec, merge_vision_callouts
from .reconcile import build_spec
from .vision import (
    AnthropicVisionClient,
    VisionCallout,
    VisionClient,
    build_prompt,
    parse_callouts,
)

__all__ = [
    "REVIEW_CONFIDENCE_FLOOR",
    "AnthropicVisionClient",
    "Assembly",
    "AttachedDimension",
    "DesignDocument",
    "ExtractionResult",
    "PageImage",
    "PartCallout",
    "Region",
    "TextBlock",
    "Unassociated",
    "UnassociatedKind",
    "VisionCallout",
    "VisionClient",
    "assemble",
    "build_prompt",
    "build_spec",
    "extract_spec",
    "ingest_design",
    "looks_like_data",
    "merge_vision_callouts",
    "parse_block",
    "parse_callouts",
]
