"""Generation-specific errors.

The pipeline never swallows a failure: unresolvable parts are routed to
escalation packages, everything else raises and blocks the job.
"""

from __future__ import annotations


class GenerationError(Exception):
    """Base class for generation failures."""


class NotApprovedError(GenerationError):
    """A spec that has not passed the review gate reached the generator.

    Enforced at the generator entry point as defense in depth: the schema
    already refuses to construct an approved spec with open questions or
    unreviewed values (blueprint §4), and the gate re-checks on the way in.
    """


class UnresolvablePartError(GenerationError):
    """A part the v1 primitive library cannot resolve into exact geometry.

    Carries the reasons so the pipeline can emit a complete escalation
    package instead of fabricating geometry (blueprint §3).
    """

    def __init__(self, part_id: str, reasons: list[str]) -> None:
        self.part_id = part_id
        self.reasons = reasons
        super().__init__(f"part '{part_id}' cannot be resolved: " + "; ".join(reasons))


class FabricationError(GenerationError):
    """A fabrication-file check failed; outputs are not safe to ship.

    Raised for stock overflow and for verification failures after files
    are written — in both cases the written outputs are removed before
    the error propagates, so nothing unsafe leaves the pipeline.
    """

    def __init__(self, message: str, failures: list[str] | None = None) -> None:
        self.failures = failures or []
        super().__init__(message)
