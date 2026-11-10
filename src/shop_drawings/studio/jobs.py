"""Jobs: one uploaded design file and everything built from it.

A job is a directory under the studio's jobs root:

    jobs/<job-id>/cabinet.pdf    the uploaded design, original name kept
                                 (the extracted spec cites it as its source)
    jobs/<job-id>/spec.json      the extracted draft spec (review-ready jobs)
    jobs/<job-id>/pages/page-N…  rendered source pages for review crops
    jobs/<job-id>/outputs/       fabrication files (.3dm, DXF sheets, cut
                                 list, escalation packages — generated jobs)
    jobs/<job-id>/generation.json  what generation produced, or why it
                                 failed (generated jobs)
    jobs/<job-id>/error.json     why extraction failed (failed jobs)

Job status is derived from what is on disk — a job is review-ready when its
spec exists, failed when an error record exists — so the listing cannot
disagree with the files, and a crash mid-upload leaves an unlisted partial
directory rather than a job that lies about its state.
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from pydantic import BaseModel

from shop_drawings.generation.pipeline import GenerationResult, generate
from shop_drawings.schema import FurnitureSpec

logger = logging.getLogger(__name__)

#: Intake policy: the design inputs the v1 extractor reads (ingest.py's
#: set), enforced before a job directory is ever created.
ALLOWED_SUFFIXES: Final[frozenset[str]] = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".webp"})
MAX_UPLOAD_BYTES: Final[int] = 25 * 1024 * 1024

TYPE_ERROR: Final[str] = (
    "That file type isn't supported yet — upload a PDF drawing, or a PNG, JPG,"
    " or WEBP photo of one."
)
SIZE_ERROR: Final[str] = (
    "That file is larger than 25 MB. Export just the drawing pages (or"
    " compress the PDF) and try again."
)
EMPTY_ERROR: Final[str] = "That file came through empty — nothing was uploaded."

#: PageImage media type → file suffix the review crop finder looks for.
_PAGE_SUFFIXES: Final[dict[str, str]] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}

_ERROR_VERSION: Final = 1


def new_job_id() -> str:
    """Sortable, collision-safe job id: UTC timestamp plus a short random."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def safe_source_name(original: str) -> str:
    """The uploaded file's name, flattened to something safe on disk.

    Keeps the real name (the spec cites it as the value source) but strips
    any path and replaces characters the filesystem would not want.
    """
    name = Path(original).name or "design"
    flattened = "".join(c if c.isalnum() or c in "._- " else "-" for c in name)
    flattened = " ".join(flattened.split()).strip(".- ")
    return flattened or "design"


class JobStatus(StrEnum):
    """Derived job state, from the files on disk."""

    REVIEW = "review"
    FAILED = "failed"


class JobSummary(BaseModel):
    """One row of the landing page's recent-jobs list."""

    job_id: str
    status: JobStatus
    source_name: str
    project_name: str | None = None
    review_status: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class JobPaths:
    """Where one job's files live."""

    jobs_dir: Path
    job_id: str

    @property
    def root(self) -> Path:
        return self.jobs_dir / self.job_id

    @property
    def spec(self) -> Path:
        return self.root / "spec.json"

    @property
    def vision(self) -> Path:
        return self.root / "vision.json"

    @property
    def error(self) -> Path:
        return self.root / "error.json"

    @property
    def pages(self) -> Path:
        return self.root / "pages"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @property
    def generation(self) -> Path:
        return self.root / "generation.json"

    def source_for(self, name: str) -> Path:
        return self.root / safe_source_name(name)

    def saved_source(self) -> Path | None:
        """The uploaded source file, whatever it ended up being named."""
        if not self.root.is_dir():
            return None
        for entry in sorted(self.root.iterdir()):
            if entry.is_file() and not entry.name.endswith(".json"):
                return entry
        return None


def write_spec(paths: JobPaths, spec_json: str) -> None:
    """Persist the extracted spec, marking the job review-ready."""
    paths.spec.write_text(spec_json, encoding="utf-8")


def write_vision(paths: JobPaths, report: dict[str, object]) -> None:
    """Persist the vision pass record the review UI renders.

    Written for every job — ``enabled: false`` is information too: the
    reviewer sees that the text-only path ran, and why.
    """
    paths.vision.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def read_vision(paths: JobPaths) -> dict[str, object] | None:
    """The job's vision record, or ``None`` when absent/unreadable.

    An unreadable record is logged and reported as absent — the review UI
    hides the vision panel rather than showing a broken job.
    """
    if not paths.vision.exists():
        return None
    try:
        raw = json.loads(paths.vision.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("unreadable vision record at %s: %s", paths.vision, exc)
        return None
    return raw if isinstance(raw, dict) else None


def write_pages(paths: JobPaths, page_images: list[tuple[int, str, bytes]]) -> None:
    """Save rendered source pages for the review UI's crops.

    Each entry is ``(page number, media type, png/jpeg/webp bytes)``; the
    suffix follows the media type so the review app's crop finder (which
    tries .png, .jpg, .jpeg, .webp) serves them with the right type.
    """
    paths.pages.mkdir(parents=True, exist_ok=True)
    for number, media_type, data in page_images:
        suffix = _PAGE_SUFFIXES.get(media_type, ".png")
        (paths.pages / f"page-{number}{suffix}").write_bytes(data)


def record_failure(paths: JobPaths, source_name: str, error: str) -> None:
    """Record why extraction failed — the failed job's whole story."""
    payload = {
        "version": _ERROR_VERSION,
        "source_name": source_name,
        "error": error,
    }
    paths.error.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_failure(paths: JobPaths) -> tuple[str, str] | None:
    """``(source_name, error)`` for a failed job, or ``None``."""
    try:
        raw = json.loads(paths.error.read_text(encoding="utf-8"))
        return str(raw.get("source_name", "unknown")), str(raw.get("error", ""))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("unreadable error record at %s: %s", paths.error, exc)
        return None


class GenerationStatus(StrEnum):
    """Outcome recorded for a job's generation run."""

    GENERATED = "generated"
    FAILED = "failed"


#: Output file kinds, in the order the outputs page lists them.
_KIND_LABELS: Final[dict[str, str]] = {
    "model": "3D model (.3dm)",
    "sheet": "Cut sheet (DXF)",
    "cutlist": "Cut list (CSV)",
    "escalation": "Escalation package",
}


def _generation_record(result: GenerationResult, out_dir: Path) -> dict[str, object]:
    """What one successful generation produced, as a JSON-ready record.

    File names are relative to ``out_dir`` — the download route serves by
    these names and nothing else.
    """
    files: list[dict[str, str]] = []
    if result.model_path is not None:
        files.append({"name": result.model_path.name, "kind": "model"})
    for sheet in result.sheet_paths:
        files.append({"name": sheet.name, "kind": "sheet"})
    if result.cut_list_path is not None:
        files.append({"name": result.cut_list_path.name, "kind": "cutlist"})
    for escalation in result.escalations:
        for path in escalation.files:
            files.append({"name": path.relative_to(out_dir).as_posix(), "kind": "escalation"})
    return {
        "version": 1,
        "status": GenerationStatus.GENERATED.value,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": files,
        "escalations": [
            {"part_id": escalation.part_id, "reasons": list(escalation.reasons)}
            for escalation in result.escalations
        ],
        "checks": [
            {
                "name": check.name,
                "status": check.status.value,
                "failures": list(check.failures),
                "notes": list(check.notes),
            }
            for check in result.checks
        ],
    }


def output_labels(record: dict[str, object]) -> list[tuple[str, str]]:
    """``(label, file name)`` pairs for a generation record, display order.

    The one place output labels are decided — the review UI's download
    links and the outputs page both render from this.
    """
    raw_files = record.get("files")
    if not isinstance(raw_files, list):
        return []
    counters: dict[str, int] = {}
    labels: list[tuple[str, str]] = []
    for entry in raw_files:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", ""))
        name = str(entry.get("name", ""))
        if not name:
            continue
        counters[kind] = counters.get(kind, 0) + 1
        labels.append((_label_for(kind, name, counters[kind]), name))
    return labels


def _label_for(kind: str, name: str, count: int) -> str:
    base = _KIND_LABELS.get(kind, "Output")
    if kind == "sheet":
        return f"{base} {count}"
    if kind == "escalation":
        # names are ``escalation/<part-id>/<file>``
        parts = name.split("/")
        part_id = parts[1] if len(parts) > 1 else name
        return f"{base} — {part_id}"
    return base


def run_generation(paths: JobPaths, spec: FurnitureSpec) -> dict[str, object]:
    """Run the generation pipeline for an approved spec, storing the outputs.

    Writes the .3dm, DXF cut sheets, cut list, and any escalation packages
    under ``outputs/``, and the run's record to ``generation.json``. Any
    prior outputs are removed first — a stale file must never survive to
    masquerade as this spec's output. Raises on failure with nothing left
    behind (``generate`` removes its own files on a failed check); the
    caller records the failure.
    """
    if paths.outputs.exists():
        shutil.rmtree(paths.outputs)
    record = _generation_record(generate(spec, out_dir=paths.outputs), paths.outputs)
    write_generation_record(paths, record)
    return record


def record_generation_failure(paths: JobPaths, error: str) -> None:
    """Record why generation failed — and leave no partial outputs behind."""
    if paths.outputs.exists():
        shutil.rmtree(paths.outputs)
    write_generation_record(
        paths,
        {
            "version": 1,
            "status": GenerationStatus.FAILED.value,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "error": error,
        },
    )


def write_generation_record(paths: JobPaths, record: dict[str, object]) -> None:
    """Persist the generation run's record beside the job's other records."""
    paths.generation.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def read_generation_record(paths: JobPaths) -> dict[str, object] | None:
    """The job's generation record, or ``None`` when absent/unreadable.

    An unreadable record is logged and reported as absent — the UI hides
    the outputs panel rather than showing a broken job.
    """
    if not paths.generation.exists():
        return None
    try:
        raw = json.loads(paths.generation.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("unreadable generation record at %s: %s", paths.generation, exc)
        return None
    return raw if isinstance(raw, dict) else None


def summarize(jobs_dir: Path, job_id: str) -> JobSummary | None:
    """One job's listing row, derived from its files — or None if unknown."""
    paths = JobPaths(jobs_dir, job_id)
    if paths.spec.exists():
        project_name, review_status = _read_spec_fields(paths.spec)
        source = paths.saved_source()
        return JobSummary(
            job_id=job_id,
            status=JobStatus.REVIEW,
            source_name=source.name if source else "unknown",
            project_name=project_name,
            review_status=review_status,
        )
    failure = read_failure(paths)
    if failure is not None:
        source_name, error = failure
        return JobSummary(
            job_id=job_id, status=JobStatus.FAILED, source_name=source_name, error=error
        )
    return None


def list_jobs(jobs_dir: Path) -> list[JobSummary]:
    """Every complete job, newest first (ids sort by creation time)."""
    if not jobs_dir.is_dir():
        return []
    summaries: list[JobSummary] = []
    for entry in sorted(jobs_dir.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        summary = summarize(jobs_dir, entry.name)
        if summary is not None:
            summaries.append(summary)
    return summaries


def _read_spec_fields(spec_path: Path) -> tuple[str | None, str | None]:
    """Project name and review status from a spec file, tolerantly.

    A spec that fails to parse here still gets listed (the review page
    reports the real problem when opened); a broken listing should never
    hide a job.
    """
    try:
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("unreadable spec at %s: %s", spec_path, exc)
        return None, None
    project = raw.get("project") or {}
    name = project.get("name")
    status = raw.get("status")
    return (str(name) if name else None), (str(status) if status else None)
