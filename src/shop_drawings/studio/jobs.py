"""Jobs: one uploaded design file and everything built from it.

A job is a directory under the studio's jobs root:

    jobs/<job-id>/cabinet.pdf    the uploaded design, original name kept
                                 (the extracted spec cites it as its source)
    jobs/<job-id>/spec.json      the extracted draft spec (review-ready jobs)
    jobs/<job-id>/pages/page-N…  rendered source pages for review crops
    jobs/<job-id>/error.json     why extraction failed (failed jobs)

Job status is derived from what is on disk — a job is review-ready when its
spec exists, failed when an error record exists — so the listing cannot
disagree with the files, and a crash mid-upload leaves an unlisted partial
directory rather than a job that lies about its state.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from pydantic import BaseModel

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
