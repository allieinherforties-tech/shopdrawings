"""Deterministic callout parsing: one text line in, typed findings out.

This module is what "AI reads" means for text-layer PDFs: every construct it
understands is a named, testable regex, and every line it cannot understand
comes back as ``is_empty`` so the caller can raise a review question instead
of a guess (locked decision: construction is never silently inferred).

Callout grammar v1 — the schedule-and-title-block style shop drawings use:

- ``SIDE PANEL — QTY 2``                     part label + quantity
- ``18mm birch plywood``                     material line (+ its thickness)
- ``702 x 380 x 18``                         ordered size span
- ``OVERALL 800 x 720 x 380``                overall extents (W x H x D)
- ``OVERALL WIDTH: 800`` / ``DEPTH 380``     tagged single value
- ``Ø5 PINS @ 32, 37 FROM FRONT``            hole pattern + setback fragments
- ``EDGE BAND FRONT, TOP``                   edge treatment
- ``DADO 6mm FOR BACK PANEL``                joinery note
- ``PROJECT: ENTRY-CABINET-002``             title-block key/value
- ``800``                                    bare number — recorded, never typed
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Final

from .models import (
    CONSTRAINT_ROLES,
    DIMENSION_ROLES,
    BareNumber,
    EdgeBanding,
    LineParse,
    MaterialLine,
    ParsedConstraint,
    ParsedDimension,
    ParsedFeature,
    PartLabel,
    SizeSpan,
    TextBlock,
    TitleKV,
    VariantDimension,
)

_NUM = r"(?P<num>\d+(?:\.\d+)?)"
_UNIT = r"(?:\s*(?P<unit>MM|CM|IN)\b)?"
_X = r"\s*[X×]\s*"

# Confidence per construct: how strongly the text itself states the meaning.
_CONF_TITLE_KV: Final = 0.97
_CONF_LABEL: Final = 0.95
_CONF_TAGGED: Final = 0.95
_CONF_OVERALL: Final = 0.95
_CONF_MATERIAL: Final = 0.9
_CONF_SIZE_SPAN: Final = 0.85
_CONF_HOLE_PATTERN: Final = 0.9
_CONF_SETBACK: Final = 0.85
_CONF_EDGES: Final = 0.9
_CONF_VARIANT: Final = 0.85
_CONF_BARE: Final = 0.5

_TITLE_KEYS: Final = (
    "PROJECT",
    "JOB",
    "CLIENT",
    "UNITS",
    "SCALE",
    "DATE",
    "SHEET",
    "DRAWN BY",
    "DRAWN",
    "REV",
    "REVISION",
)
_TITLE_KV_RE: Final = re.compile(
    rf"^(?P<key>{'|'.join(_TITLE_KEYS)})(?:\s*[:.]\s*|\s+)(?P<value>\S.*?)\s*$",
    re.IGNORECASE,
)

# Lines that look like data but are sheet furniture — never questions, never labels.
_SHEET_NOISE_RE: Final = re.compile(
    r"^(?:SHOP\s+DRAWINGS|DETAIL\s+\d+|SECTION\s+[A-Z0-9]|NOTES?\b|QTY\s*\d+)",
    re.IGNORECASE,
)

_MATERIAL_RE: Final = re.compile(
    rf"^{_NUM}\s*(?P<unit>MM|CM|IN)\b\s+(?P<material>[A-Z][A-Z0-9 ,/&-]+?)\s*$",
    re.IGNORECASE,
)

_SIZE_SPAN_RE: Final = re.compile(
    r"^(?P<num>\d+(?:\.\d+)?)\s*[X×]\s*(?P<num_2>\d+(?:\.\d+)?)"
    r"(?:\s*[X×]\s*(?P<num_3>\d+(?:\.\d+)?))?(?:\s*(?P<unit>MM|CM|IN)\b)?\s*$",
    re.IGNORECASE,
)

_TAGGED_RE: Final = re.compile(
    rf"^(?P<overall>OVERALL\s+)?(?P<kind>WIDTH|HEIGHT|DEPTH|LENGTH|THICKNESS|THK)\s*[:=.\-]?\s*"
    rf"{_NUM}{_UNIT}\s*$",
    re.IGNORECASE,
)

_OVERALL_SPAN_RE: Final = re.compile(
    r"^OVERALL\s*[:=]?\s*(?P<num>\d+(?:\.\d+)?)\s*[X×]\s*(?P<num_2>\d+(?:\.\d+)?)"
    r"\s*[X×]\s*(?P<num_3>\d+(?:\.\d+)?)(?:\s*(?P<unit>MM|CM|IN)\b)?\s*$",
    re.IGNORECASE,
)

# Optional leading qualifier — "SHELF PIN HOLES Ø5 @ 32" — before the diameter.
_HOLE_LEAD: Final = r"(?:(?:SHELF\s+)?(?:PIN|DRILL)\s+)?(?:HOLES?|PINS?|BORES?|DRILLS?)?\s*"

_HOLE_PATTERN_RE: Final = re.compile(
    rf"^{_HOLE_LEAD}(?:Ø|⌀|DIA\.?)\s*(?P<num>\d+(?:\.\d+)?)(?:\s*(?P<unit>MM|CM|IN)\b)?"
    r"\s*(?:PINS?|HOLES?|DRILLS?|BORE)?\s*@?\s*(?P<num_2>\d+(?:\.\d+)?)"
    r"(?:\s*(?P<unit_2>MM|CM|IN)\b)?\s*(?:SYSTEM|CENTRES|CENTERS|OC|O\.C\.?)?\s*$",
    re.IGNORECASE,
)

_SETBACK_RE: Final = re.compile(
    rf"^{_NUM}{_UNIT}\s*FROM\s+(?P<ref>FRONT|BACK|LEFT|RIGHT|TOP|BOTTOM)"
    rf"(?:\s+EDGE)?\s*$",
    re.IGNORECASE,
)

_EDGES_RE: Final = re.compile(
    r"^EDGES?\s*BAND(?:ING)?\s*[:.]?\s*(?P<edges>[A-Z ,/-]+)$",
    re.IGNORECASE,
)

_FEATURE_RE: Final = re.compile(
    r"^(?:DADO|GROOVE|REBATE|RABBET|MORTISE|MORTICE|TENON|POCKET\s+HOLES?)\b",
    re.IGNORECASE,
)

# Part label: uppercase words, optional "QTY n" tail. First char a letter, so
# "Ø5 …" and "18mm …" are excluded structurally.
_LABEL_RE: Final = re.compile(
    r"^(?P<label>[A-Z][A-Z0-9 /&.\-–—]*?)[\s\-–—|(]*"
    r"(?:QTY\.?\s*[:.]?\s*(?P<qty>\d+)\s*\)?)?\s*$"
)

# Words that name a fabricated part. A caps line qualifies as a part label
# only with an explicit QTY marker or one of these words — otherwise any
# caps line with a digit ("REVEAL 3") would masquerade as a part.
_PART_WORDS: Final = {
    "APRON",
    "BACK",
    "BASE",
    "BOTTOM",
    "CARCASS",
    "CASE",
    "CENTER",
    "CENTRE",
    "COUNTERTOP",
    "DIVIDER",
    "DOOR",
    "DRAWER",
    "FACE",
    "FRAME",
    "FRONT",
    "GABLE",
    "KICK",
    "LEG",
    "PANEL",
    "PARTITION",
    "RAIL",
    "SIDE",
    "SHELF",
    "STILE",
    "STRETCHER",
    "TABLE",
    "TABLETOP",
    "TOP",
    "VALANCE",
}

_BARE_RE: Final = re.compile(rf"^{_NUM}{_UNIT}\s*$", re.IGNORECASE)

# Variant alternatives: 'H 22" OR 24"', 'DEPTH 380 OR 400'. Two numbers joined
# by OR — one line, two possible values, no way to pick. The units (if any)
# are quoted verbatim in the question; the values stay as stated.
_NUMBER: Final = r"\d+(?:\.\d+)?"
_VARIANT_RE: Final = re.compile(
    r"^(?P<lead>\D*?)"
    rf"(?P<first>{_NUMBER})(?:\s*(?:MM|CM|IN)\b|[\"”″'])?\s*"
    r"\bOR\b"
    rf"\s*(?P<second>{_NUMBER})(?:\s*(?:MM|CM|IN)\b|[\"”″'])?\s*$",
    re.IGNORECASE,
)

_EDGE_VALUES: Final = {"FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM"}

_KIND_ALIASES: Final = {"THK": "thickness"}
_FEATURE_ALIASES: Final = {
    "RABBET": "rebate",
    "MORTICE": "mortise",
    "POCKET HOLE": "pocket_hole",
    "POCKET HOLES": "pocket_hole",
}

_LABEL_STOPLIST: Final = set(_TITLE_KEYS) | {
    "OVERALL",
    "EDGE",
    "EDGES",
    "DADO",
    "GROOVE",
    "REBATE",
    "RABBET",
    "MORTISE",
    "MORTICE",
    "TENON",
    "POCKET",
    "DETAIL",
    "SECTION",
    "NOTES",
    "NOTE",
    "JOINERY",
}


def _mm(value: str | float, unit: str | None) -> float:
    """Value with an explicit unit suffix, converted to millimetres."""
    scale = {"MM": 1.0, "CM": 10.0, "IN": 25.4}[unit.upper()] if unit else 1.0
    return float(value) * scale


def _is_narrative_prose(text: str) -> bool:
    """Narrative prose (mixed case, no digits) is annotation, not data.

    Drawings encode data as numbers and SHOUTING CAPS; "Shop Drawings — Entry
    Cabinet" is a heading. Ignoring it is not a silent drop of information.
    """
    has_digit = any(ch.isdigit() for ch in text)
    is_shouting = text == text.upper() and any(ch.isalpha() for ch in text)
    return not has_digit and not is_shouting


def parse_block(block: TextBlock) -> LineParse:
    """Parse one text block into typed findings; unmatched input yields ``is_empty``."""
    text = block.text.strip()
    if not text:
        return LineParse(block=block)

    # 1. Title-block key/value: "PROJECT: ENTRY-CABINET-002", "UNITS MM".
    match = _TITLE_KV_RE.match(text)
    if match:
        kv = TitleKV(
            key=match.group("key").upper(),
            value=match.group("value").strip(),
            block=block,
            confidence=_CONF_TITLE_KV,
        )
        return LineParse(block=block, title_kv=kv)

    if _SHEET_NOISE_RE.match(text):
        return LineParse(block=block)

    # 1b. Variant alternatives: 'H 22" OR 24"' — a question, never a pick.
    match = _VARIANT_RE.match(text)
    if match:
        return LineParse(
            block=block,
            variant=VariantDimension(
                values=(float(match.group("first")), float(match.group("second"))),
                block=block,
                confidence=_CONF_VARIANT,
            ),
        )

    # 2. Edge treatment: "EDGE BAND FRONT, TOP".
    match = _EDGES_RE.match(text)
    if match:
        edges = tuple(
            edge.strip().upper()
            for edge in match.group("edges").replace("AND", ",").split(",")
            if edge.strip().upper() in _EDGE_VALUES
        )
        if edges:
            return LineParse(
                block=block,
                edge_banding=EdgeBanding(edges=edges, block=block, confidence=_CONF_EDGES),
            )

    # 3. Material line: "18mm birch plywood" — material name + its thickness.
    match = _MATERIAL_RE.match(text)
    if match:
        return LineParse(
            block=block,
            material=MaterialLine(
                material=match.group("material").strip().lower(),
                thickness=_mm(match.group("num"), match.group("unit")),
                block=block,
                confidence=_CONF_MATERIAL,
            ),
        )

    # 4. Hole pattern and setback — a line may carry several comma fragments,
    #    e.g. "Ø5 PINS @ 32, 37 FROM FRONT".
    fragments = [f.strip() for f in text.split(",")]
    dims: list[ParsedDimension] = []
    for fragment in fragments:
        hole = _HOLE_PATTERN_RE.match(fragment)
        if hole:
            dims.append(
                ParsedDimension(
                    kind="hole_diameter",
                    value=_mm(hole.group("num"), hole.group("unit")),
                    block=block,
                    confidence=_CONF_HOLE_PATTERN,
                )
            )
            dims.append(
                ParsedDimension(
                    kind="spacing",
                    value=_mm(hole.group("num_2"), hole.group("unit_2")),
                    block=block,
                    confidence=_CONF_HOLE_PATTERN,
                )
            )
            continue
        setback = _SETBACK_RE.match(fragment)
        if setback:
            dims.append(
                ParsedDimension(
                    kind="setback",
                    value=_mm(setback.group("num"), setback.group("unit")),
                    block=block,
                    confidence=_CONF_SETBACK,
                    reference=setback.group("ref").lower() + "_edge",
                )
            )
    if dims:
        return LineParse(block=block, dimensions=tuple(dims))

    # 5. Joinery note: "DADO 6mm FOR BACK PANEL".
    match = _FEATURE_RE.match(text)
    if match:
        raw_type = re.sub(r"\s+", " ", text.split()[0]).upper()
        return LineParse(
            block=block,
            feature=ParsedFeature(
                type=_FEATURE_ALIASES.get(raw_type, raw_type.lower()),
                note=text,
                dimensions=(),
                block=block,
            ),
        )

    # 6. Overall extents: "OVERALL 800 x 720 x 380" (width x height x depth).
    match = _OVERALL_SPAN_RE.match(text)
    if match:
        values = (
            _mm(match.group("num"), None),
            _mm(match.group("num_2"), None),
            _mm(match.group("num_3"), None),
        )
        scale = _mm(1, match.group("unit"))
        kinds = ("overall_width", "overall_height", "overall_depth")
        return LineParse(
            block=block,
            constraints=tuple(
                ParsedConstraint(type=k, value=v * scale, block=block, confidence=_CONF_OVERALL)
                for k, v in zip(kinds, values, strict=True)
            ),
        )

    # 7. Tagged single value: "OVERALL WIDTH: 800", "DEPTH 380", "THK 18".
    match = _TAGGED_RE.match(text)
    if match:
        raw_kind = match.group("kind").upper()
        kind = _KIND_ALIASES.get(raw_kind, raw_kind.lower())
        value = _mm(match.group("num"), match.group("unit"))
        if match.group("overall"):
            return LineParse(
                block=block,
                constraints=(
                    ParsedConstraint(
                        type=f"overall_{kind}", value=value, block=block, confidence=_CONF_OVERALL
                    ),
                ),
            )
        return LineParse(
            block=block,
            dimensions=(
                ParsedDimension(kind=kind, value=value, block=block, confidence=_CONF_TAGGED),
            ),
        )

    # 8. Size span: "702 x 380 x 18" — ordered; kinds decided with a material line.
    match = _SIZE_SPAN_RE.match(text)
    if match:
        span_values = [_mm(match.group("num"), None), _mm(match.group("num_2"), None)]
        confidence = _CONF_SIZE_SPAN
        third = match.group("num_3")
        if third is not None:
            span_values.append(_mm(third, None))
            confidence += 0.05  # a triple self-identifies its thickness
        scale = _mm(1, match.group("unit"))
        if scale != 1.0:
            span_values = [v * scale for v in span_values]
        return LineParse(
            block=block,
            sizes=SizeSpan(
                values=tuple(span_values), block=block, confidence=min(confidence, 0.95)
            ),
        )

    # 9. Part label: uppercase words, optional QTY tail, not a stoplisted
    # keyword, and either QTY-marked or carrying a known part word.
    match = _LABEL_RE.match(text)
    if match:
        label = match.group("label").strip().strip("-–—(| ")
        words = label.split()
        qty_text = match.group("qty")
        if (
            label
            and words
            and words[0].rstrip(":.").upper() not in _LABEL_STOPLIST
            and _is_part_label(label, qty_text is not None)
        ):
            return LineParse(
                block=block,
                part_label=PartLabel(
                    label=label,
                    quantity=int(qty_text) if qty_text else 1,
                    quantity_stated=qty_text is not None,
                    block=block,
                    confidence=_CONF_LABEL,
                ),
            )

    # 10. Bare number: real data, role unknown — recorded, never typed here.
    match = _BARE_RE.match(text)
    if match:
        return LineParse(
            block=block,
            bare_number=BareNumber(
                value=_mm(match.group("num"), match.group("unit")),
                block=block,
                confidence=_CONF_BARE,
            ),
        )

    # Unmatched: the caller decides — question if it looks like data, else prose.
    return LineParse(block=block)


def _is_part_label(label: str, has_qty: bool) -> bool:
    """A label names a part: explicit quantity or a known part word."""
    if has_qty:
        return True
    words = set(label.upper().replace("-", " ").split())
    return bool(words & _PART_WORDS)


def looks_like_data(block: TextBlock) -> bool:
    """True when an unmatched line deserves a review question.

    Sheet furniture ("NOTES: …", "DETAIL 2") and narrative prose do not —
    asking about them would bury real questions in noise.
    """
    if _SHEET_NOISE_RE.match(block.text.strip()):
        return False
    return not _is_narrative_prose(block.text)


_ROLE_TARGETS: Final[dict[str, frozenset[str]]] = {
    "part_label": frozenset({"part_label"}),
    "material": frozenset({"material"}),
    "edge_banding": frozenset({"edge_banding"}),
    "feature": frozenset(),
    "title": frozenset({"title_kv"}),
}
_ALL_TARGETS: Final = frozenset(
    {"title_kv", "part_label", "material", "sizes", "constraints", "dimensions", "edge_banding"}
)


def apply_block_overrides(parsed: LineParse) -> LineParse:
    """Fold a vision model's confidence cap and role tag into a parsed line.

    A block a vision client vouched for carries ``vision_confidence`` and maybe
    a ``role``. The model's confidence caps every finding it touches — the
    pipeline is never more confident than its least certain reader. A role on
    a block whose text parsed as a bare number types that number: the model
    saw which geometry the leader touches, which is exactly what a text layer
    cannot carry. Roles never *raise* confidence above what the grammar allows.
    """
    block = parsed.block
    cap = block.vision_confidence
    if cap is None:
        return parsed

    role = block.role
    if role in CONSTRAINT_ROLES and parsed.bare_number is not None:
        bare = parsed.bare_number
        return LineParse(
            block=block,
            constraints=(
                ParsedConstraint(type=role, value=bare.value, block=block, confidence=cap),
            ),
        )
    if role in DIMENSION_ROLES and parsed.bare_number is not None:
        bare = parsed.bare_number
        return LineParse(
            block=block,
            dimensions=(ParsedDimension(kind=role, value=bare.value, block=block, confidence=cap),),
        )

    targets = _ROLE_TARGETS.get(role or "", _ALL_TARGETS)

    def capped(confidence: float) -> float:
        return min(cap, confidence)

    title_kv = parsed.title_kv
    if title_kv is not None and "title_kv" in targets:
        title_kv = replace(title_kv, confidence=capped(title_kv.confidence))
    part_label = parsed.part_label
    if part_label is not None and "part_label" in targets:
        part_label = replace(part_label, confidence=capped(part_label.confidence))
    material = parsed.material
    if material is not None and "material" in targets:
        material = replace(material, confidence=capped(material.confidence))
    sizes = parsed.sizes
    if sizes is not None and "sizes" in targets:
        sizes = replace(sizes, confidence=capped(sizes.confidence))
    constraints = (
        tuple(
            replace(c, confidence=capped(c.confidence))
            for c in parsed.constraints
            if "constraints" in targets
        )
        or parsed.constraints
    )
    dimensions = (
        tuple(
            replace(d, confidence=capped(d.confidence))
            for d in parsed.dimensions
            if "dimensions" in targets
        )
        or parsed.dimensions
    )
    edge_banding = parsed.edge_banding
    if edge_banding is not None and "edge_banding" in targets:
        edge_banding = replace(edge_banding, confidence=capped(edge_banding.confidence))

    return LineParse(
        block=block,
        title_kv=title_kv,
        part_label=part_label,
        material=material,
        sizes=sizes,
        constraints=constraints,
        dimensions=dimensions,
        feature=parsed.feature,
        edge_banding=edge_banding,
        bare_number=parsed.bare_number,
        variant=parsed.variant,
    )
