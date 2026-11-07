"""The review store: spec read/write with the gate enforced by the schema.

Every mutation goes through the real ``FurnitureSpec`` model — the review
gate lives in the schema's validator, so the store cannot approve a spec
with open questions or unreviewed values even by accident. Corrections are
written back into the spec file; the review log (JSONL beside it) records
that they happened.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from shop_drawings.review.models import LogEntry, ValueAddress, dimension_map, feature_label
from shop_drawings.schema import (
    Dimension,
    FurnitureSpec,
    ReviewStatus,
)


class GateError(Exception):
    """The requested state change fails the review gate or store policy."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ReviewStore:
    """Owns one spec file and its review log."""

    def __init__(self, spec_path: Path) -> None:
        self.spec_path = spec_path
        self.log_path = spec_path.with_suffix(".review-log.jsonl")
        self._spec: FurnitureSpec = self._read()

    # -- reads ---------------------------------------------------------

    @property
    def spec(self) -> FurnitureSpec:
        return self._spec

    def _read(self) -> FurnitureSpec:
        raw = self.spec_path.read_text(encoding="utf-8")
        return FurnitureSpec.model_validate_json(raw)

    def reread(self) -> FurnitureSpec:
        """Reload from disk (another process may have edited the file)."""
        self._spec = self._read()
        return self._spec

    def read_log(self) -> list[LogEntry]:
        if not self.log_path.exists():
            return []
        entries: list[LogEntry] = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(LogEntry.model_validate_json(line))
        return entries

    # -- writes --------------------------------------------------------

    def _log(self, entry: LogEntry) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def _save(self, spec: FurnitureSpec) -> None:
        """Persist the spec, revalidating through the schema on the way out."""
        validated = FurnitureSpec.model_validate(spec.model_dump())
        self.spec_path.write_text(validated.model_dump_json(indent=2) + "\n", encoding="utf-8")
        self._spec = validated

    def _rebuild(self, **updates: object) -> FurnitureSpec:
        """A new spec with updates applied — the schema validator runs here.

        This is the gate: constructing an ``approved`` spec with open
        questions or unreviewed values raises, exactly as the blueprint
        requires. Nothing in this module can bypass it.
        """
        data = self._spec.model_dump()
        data.update(updates)
        return FurnitureSpec.model_validate(data)

    # -- reviewer actions ----------------------------------------------

    def resolve_question(self, index: int, answer: str) -> FurnitureSpec:
        """Answer a blocked question in plain language; it leaves the queue.

        The answer is recorded in the review log; if it changes a value,
        that correction is made separately and persists in the spec.
        """
        questions = list(self._spec.unresolved_questions)
        if not 0 <= index < len(questions):
            raise IndexError(f"no unresolved question at index {index}")
        if not answer.strip():
            raise ValueError("an answer is required to resolve a question")
        question = questions.pop(index)
        self._spec = self._rebuild(unresolved_questions=questions)
        self._save(self._spec)
        self._log(
            LogEntry(
                ts=_now(),
                action="question_resolved",
                detail=f"{question} — answered: {answer.strip()}",
            )
        )
        return self._spec

    def correct_value(
        self,
        address: ValueAddress,
        value: float | None = None,
        status: ReviewStatus | None = None,
    ) -> FurnitureSpec:
        """Retype a value and/or set its review status. Both persist.

        The correction keeps the original source reference — the value
        still traces back to the crop it was read from; the log records
        what the reviewer changed.
        """
        if self._spec.status is ReviewStatus.APPROVED:
            raise GateError(["spec is approved — corrections are locked; reopen review first"])
        dims = dimension_map(self._spec, address)
        old = dims[address.kind]
        before = old.value
        new_value = old.value if value is None else value
        if new_value < 0:
            raise ValueError("a dimension cannot be negative")
        new_status = old.status if status is None else status
        updated: Dimension = old.model_copy(update={"value": new_value, "status": new_status})
        dims[address.kind] = updated
        self._save(self._spec)
        label = feature_label(self._spec, address)
        self._log(
            LogEntry(
                ts=_now(),
                action="value_corrected",
                detail=(
                    f"{label}: value {before:g} → {new_value:g}"
                    if value is not None
                    else f"{label}: status set to {new_status.value}"
                ),
                address=address,
                before=before,
                after=new_value,
            )
        )
        return self._spec

    def set_part_status(self, part_id: str, status: ReviewStatus) -> FurnitureSpec:
        """Set a part's review status (the part itself, not its dimensions)."""
        if self._spec.status is ReviewStatus.APPROVED:
            raise GateError(["spec is approved — corrections are locked; reopen review first"])
        for part in self._spec.parts:
            if part.id == part_id:
                break
        else:
            raise KeyError(part_id)
        part.status = status
        self._save(self._spec)
        self._log(
            LogEntry(
                ts=_now(),
                action="part_status_set",
                detail=f"part '{part_id}' → {status.value}",
            )
        )
        return self._spec

    def set_constraint_status(self, index: int, status: ReviewStatus) -> FurnitureSpec:
        """Set an assembly constraint's review status."""
        if self._spec.status is ReviewStatus.APPROVED:
            raise GateError(["spec is approved — corrections are locked; reopen review first"])
        if not 0 <= index < len(self._spec.constraints):
            raise IndexError(f"no constraint at index {index}")
        constraint = self._spec.constraints[index]
        constraint.status = status
        self._save(self._spec)
        self._log(
            LogEntry(
                ts=_now(),
                action="constraint_status_set",
                detail=(
                    f"constraint '{constraint.type.value}' ({constraint.value:g}) → {status.value}"
                ),
            )
        )
        return self._spec

    def approve(self) -> FurnitureSpec:
        """Mark the spec approved — or report exactly what blocks it.

        The gate is the schema's: ``FurnitureSpec`` refuses to exist as
        approved while questions are open or values are unreviewed.
        """
        errors = self._spec.approval_errors()
        if errors:
            self._log(
                LogEntry(
                    ts=_now(),
                    action="approve_rejected",
                    detail="; ".join(errors),
                )
            )
            raise GateError(errors)
        self._spec = self._rebuild(status=ReviewStatus.APPROVED)
        self._save(self._spec)
        self._log(LogEntry(ts=_now(), action="spec_approved", detail="review gate passed"))
        return self._spec
