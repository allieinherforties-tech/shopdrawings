"""Deterministic fabrication generation for approved furniture specs.

Public API: :func:`generate` — an approved :class:`FurnitureSpec` in, a
.3dm model, DXF cut sheets, and a cut list out, verified before anything
ships. Studio conventions live in :class:`GenerationConfig`.
"""

from shop_drawings.generation.config import GenerationConfig
from shop_drawings.generation.errors import FabricationError, NotApprovedError
from shop_drawings.generation.pipeline import GenerationResult, generate

__all__ = [
    "FabricationError",
    "GenerationConfig",
    "GenerationResult",
    "NotApprovedError",
    "generate",
]
