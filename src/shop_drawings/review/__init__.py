"""The review gate: human review of an extracted spec before generation.

Blueprint §4 — the gate is where trust is manufactured. The UI presents
the spec in priority order (blocked questions, conflicts, low-confidence
associations, batch confirm), writes corrections back into the spec, and
the schema enforces that nothing reaches `approved` with open questions.
"""

from shop_drawings.review.app import create_app
from shop_drawings.review.store import GateError, ReviewStore

__all__ = ["GateError", "ReviewStore", "create_app"]
