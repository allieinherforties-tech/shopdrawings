"""Issue and report types for the validation layer.

Every blocking problem is a :class:`ValidationIssue` carrying a rule id, a
plain-language message, and the *evidence*: each value involved, with its
source reference, so the reviewer sees both sides of a failed check — the
blueprint's "both values shown" requirement (§4, §5).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from shop_drawings.schema import SourceReference


class RuleId(StrEnum):
    """Which named check raised an issue."""

    CONSTRAINT_CLOSURE = "constraint_closure"
    GEOMETRIC_SANITY = "geometric_sanity"
    CONFLICT = "conflict"
    TRACEABILITY = "traceability"
    REVIEW_GATE = "review_gate"


class ValueEvidence(BaseModel):
    """One value involved in an issue, tied back to where it was read from.

    ``location`` names the part, feature, or constraint the value belongs to;
    ``source`` is the crop of the source design the value was extracted from
    (absent only for derived values, such as a computed sum of parts).
    """

    model_config = ConfigDict(extra="forbid")

    location: str = Field(description="human-readable place in the spec, e.g. \"part 'side_left'\"")
    role: str = Field(description="what the value claims to be, e.g. 'overall_width' or 'width'")
    value: float | None = Field(default=None, description="the value, in the project's units")
    source: SourceReference | None = None
    detail: str | None = Field(default=None, description="extra context, e.g. how a sum was made")


class ValidationIssue(BaseModel):
    """A single blocking problem, with every conflicting value attached."""

    model_config = ConfigDict(extra="forbid")

    rule: RuleId
    message: str
    evidence: list[ValueEvidence] = Field(default_factory=list)


class ValidationReport(BaseModel):
    """The result of validating one spec: every issue, in stable order."""

    model_config = ConfigDict(extra="forbid")

    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def blocking(self) -> bool:
        """True when any issue blocks the spec from reaching approval."""
        return bool(self.issues)
