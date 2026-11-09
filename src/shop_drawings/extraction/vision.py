"""The vision path: page rasters in, role-tagged callouts out.

For text-layer PDFs the deterministic parser already reads the words; the
vision client exists for what the text layer cannot say — which number
belongs to which part, and what a sketched or photographed design says. Its
findings are *callouts*: text + region + role + a part label + the model's
own confidence. They enter the same deterministic pipeline as text-layer
lines (same parser, same confidence floor), so the vision model never types
a value the grammar cannot also express — blueprint §1: AI reads, code draws.

The client is a protocol; ``AnthropicVisionClient`` is the wired
implementation and any test (or future local model) can satisfy the same
interface without network access.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Final, Protocol

from .models import KNOWN_ROLES, DesignDocument

_MAX_TOKENS: Final = 16384

#: Vision model used when ANTHROPIC_API_KEY is configured. The env key gate is
#: the only configuration read; without it nothing in the pipeline changes.
VISION_MODEL: Final = "claude-sonnet-5"


def vision_client_from_env(model: str = VISION_MODEL) -> VisionClient | None:
    """A vision client when a key is configured, else ``None``.

    The one place the ANTHROPIC_API_KEY gate is read: callers pass the result
    straight through — ``None`` means the deterministic text-only path runs
    exactly as it always has.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return AnthropicVisionClient(model=model)


class VisionError(RuntimeError):
    """The vision client could not produce a usable response."""


class VisionNotConfiguredError(VisionError):
    """No API key is available for the configured vision client."""


@dataclass(frozen=True)
class VisionCallout:
    """One finding the vision model reports from a rendered page.

    ``role`` is ``None`` when the model reports text it cannot classify —
    the callout still flows through the deterministic grammar.
    """

    text: str
    page: int
    region: tuple[float, float, float, float]
    role: str | None
    part_label: str | None
    confidence: float

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("vision callout text must not be empty")
        if self.page < 1:
            raise ValueError(f"vision callout page must be >= 1, got {self.page}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"vision callout confidence must be in [0, 1], got {self.confidence}")


class VisionClient(Protocol):
    """Anything that can read rendered pages and report callouts."""

    def extract(self, document: DesignDocument) -> tuple[VisionCallout, ...]:
        """Read every page image; report role-tagged callouts."""
        ...  # pragma: no cover


def build_prompt() -> str:
    """The extraction instruction sent with every page image."""
    roles = ", ".join(KNOWN_ROLES)
    return (
        "You are reading a furniture shop drawing (or a sketch/photo of one). "
        "Report every piece of fabrication information you can read: dimensions, "
        "part labels, material callouts, edge treatment, joinery notes, and "
        "title-block entries.\n"
        "Answer with ONLY a JSON object of this exact shape:\n"
        '{"callouts": [{"text": "<exact text as read>", "page": <1-based page>, '
        '"region": [x0, y0, x1, y1], "role": "<role>", "part_label": <label or null>, '
        '"confidence": <0..1>}]}\n'
        "Coordinates are normalized to the page with the origin at the TOP-LEFT "
        "(y grows downward) and bound the text you read. Roles must come from "
        f"this list: {roles}. Set part_label for any dimension you can attribute "
        "to a named part; otherwise null. Use your true confidence; do not guess "
        "meanings you cannot see. Do not invent callouts."
    )


def parse_callouts(payload: str) -> tuple[VisionCallout, ...]:
    """Parse the model's JSON answer into callouts, skipping malformed entries.

    A response with no parseable JSON raises ``VisionError`` — a silent empty
    result would hide a broken client behind an empty spec.
    """
    text = payload.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start < 0:
        raise VisionError(f"vision response contained no JSON object: {payload[:200]!r}")
    try:
        data = json.loads(text[start:])
        callouts_raw = data["callouts"]
        if not isinstance(callouts_raw, list):
            raise VisionError("vision response 'callouts' is not a list")
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise VisionError(f"vision response was not valid callout JSON: {error}") from error

    callouts: list[VisionCallout] = []
    for raw in callouts_raw:
        callout = _callout_from(raw)
        if callout is not None:
            callouts.append(callout)
    return tuple(callouts)


def _callout_from(raw: object) -> VisionCallout | None:
    """Validate one entry; malformed entries are skipped (and only skipped)."""
    if not isinstance(raw, dict):
        return None
    text = raw.get("text")
    page = raw.get("page")
    region = raw.get("region")
    role = raw.get("role")
    confidence = raw.get("confidence")
    if not isinstance(text, str) or not text.strip():
        return None
    if not isinstance(page, int) or page < 1:
        return None
    if not isinstance(region, (list, tuple)) or len(region) != 4:
        return None
    if not all(isinstance(v, (int, float)) for v in region):
        return None
    if not isinstance(confidence, (int, float)):
        return None
    if not isinstance(role, str):
        return None
    # Vision models report pixel-ish boxes off by a margin; clamp into the page
    # rather than reject the whole response for one noisy crop.
    x0, y0, x1, y1 = (min(max(float(v), 0.0), 1.0) for v in region)
    if x0 >= x1 or y0 >= y1:
        return None
    part_label = raw.get("part_label")
    label_text = part_label.strip() if isinstance(part_label, str) else ""
    parsed_role = role.strip().lower()
    return VisionCallout(
        text=text.strip(),
        page=page,
        region=(x0, y0, x1, y1),
        role=parsed_role if parsed_role in KNOWN_ROLES else None,
        part_label=label_text if label_text else None,
        confidence=float(confidence),
    )


class AnthropicVisionClient:
    """``VisionClient`` backed by Anthropic's messages API.

    The SDK is imported lazily so the dependency (and its absence) never
    affects the deterministic path. The model name is configurable; the
    default suits general document reading.
    """

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self._model = model
        self._api_key = api_key

    def extract(self, document: DesignDocument) -> tuple[VisionCallout, ...]:
        import anthropic

        key = self._api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise VisionNotConfiguredError(
                "ANTHROPIC_API_KEY is not set; configure a vision client to read "
                "drawings without a text layer"
            )
        client = anthropic.Anthropic(api_key=key)
        # anthropic's content-block TypedDict union rejects hand-built dicts;
        # the boundary is contained here and per-entry callout validation
        # happens after the reply is parsed.
        response = client.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": _message_content(document)}],  # type: ignore[misc,list-item]
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return parse_callouts(text)


def _message_content(document: DesignDocument) -> list[dict[str, object]]:
    """Image blocks for every page followed by the extraction prompt."""
    content: list[dict[str, object]] = []
    for page in document.pages:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": page.media_type,
                    "data": _base64(page.png),
                },
            }
        )
    content.append({"type": "text", "text": build_prompt()})
    return content


def _base64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")
