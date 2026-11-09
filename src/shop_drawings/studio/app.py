"""The studio front door: upload a design, land in review for that design.

One FastAPI app that wires the pipeline's entry point to the review gate:

- ``/`` — landing page: drag-and-drop upload, the flow in plain language,
  recent jobs.
- ``POST /api/upload`` — validates type and size, saves the source file,
  runs extraction synchronously, writes the spec, and redirects into the
  per-job review UI. Extraction failures record ``error.json`` and redirect
  to the failure page; nothing is ever a silent guess or a traceback.
- ``/jobs/{job_id}/`` — the review gate mounted once, resolving the spec
  per request from the job id. The review app is job-agnostic, so this
  mount serves every job; gate semantics live in the store and schema,
  untouched here.
- ``/failed/{job_id}`` — what failed, why, what to do.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from shop_drawings.extraction.pipeline import extract_spec
from shop_drawings.extraction.vision import VisionClient
from shop_drawings.review.app import create_app
from shop_drawings.studio import jobs
from shop_drawings.studio.pages import failed_job_page

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

#: The multipart field name the landing page posts.
UPLOAD_FIELD = "file"


def create_studio_app(
    jobs_dir: Path,
    *,
    vision_client: VisionClient | None = None,
) -> FastAPI:
    """Build the front door over a jobs directory.

    ``vision_client`` is opt-in dependency injection: ``None`` (the default)
    runs the deterministic extraction path, which handles text-layer PDFs
    fully. The CLI entry point constructs a client only when
    ``ANTHROPIC_API_KEY`` is configured.
    """
    app = FastAPI(title="Shop Drawings — Studio", docs_url=None, redoc_url=None)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def landing() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/jobs", response_model=None)
    def recent_jobs() -> list[jobs.JobSummary]:
        return jobs.list_jobs(jobs_dir)

    def _extract(paths: jobs.JobPaths, source_path: Path) -> None:
        """Run extraction and persist the spec and rendered source pages."""
        result = extract_spec(source_path, vision_client=vision_client)
        spec = result.spec
        jobs.write_spec(paths, spec.model_dump_json(indent=2) + "\n")
        jobs.write_pages(
            paths,
            [(page.page, page.media_type, page.png) for page in result.document.pages],
        )

    @app.post("/api/upload", response_model=None)
    async def upload(file: UploadFile) -> RedirectResponse:
        # Reject before creating anything: a bad upload leaves no job.
        original = file.filename or "design"
        suffix = Path(original).suffix.lower()
        if suffix not in jobs.ALLOWED_SUFFIXES:
            raise HTTPException(status_code=422, detail=jobs.TYPE_ERROR)
        data = await file.read()
        if len(data) > jobs.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=422, detail=jobs.SIZE_ERROR)
        if not data:
            raise HTTPException(status_code=422, detail=jobs.EMPTY_ERROR)

        job_id = jobs.new_job_id()
        paths = jobs.JobPaths(jobs_dir, job_id)
        paths.root.mkdir(parents=True)
        source_path = paths.source_for(original)
        source_path.write_bytes(data)

        try:
            _extract(paths, source_path)
        except Exception as exc:
            # The failure is the user's outcome, not a server crash: record
            # exactly what happened, keep the job listed as failed, and
            # send them to an explanation page. Logged here too — recorded,
            # never swallowed.
            logger.exception("extraction failed for job %s (%s)", job_id, original)
            jobs.record_failure(paths, source_name=original, error=f"{type(exc).__name__}: {exc}")
            return RedirectResponse(f"/failed/{job_id}", status_code=303)

        return RedirectResponse(f"/jobs/{job_id}/", status_code=303)

    # Registered before the mount so the no-trailing-slash form redirects
    # instead of resolving the review UI's relative assets against the
    # wrong directory.
    @app.get("/jobs/{job_id}", response_model=None)
    def job_redirect(job_id: str) -> RedirectResponse:
        return RedirectResponse(f"/jobs/{job_id}/", status_code=307)

    def _spec_for_job(request: Request) -> Path:
        paths = _paths_for(request)
        if not paths.spec.exists():
            raise HTTPException(status_code=404, detail=f"unknown job: {paths.root.name!r}")
        return paths.spec

    def _pages_for_job(request: Request) -> Path:
        return _paths_for(request).pages

    def _paths_for(request: Request) -> jobs.JobPaths:
        job_id = str(request.path_params.get("job_id", ""))
        if not job_id or "/" in job_id:
            raise HTTPException(status_code=404, detail="unknown job")
        return jobs.JobPaths(jobs_dir, job_id)

    # One review instance serves every job: the spec and source-page
    # directories are resolved per request from the mounted job id.
    review = create_app(_spec_for_job, source_images_dir=_pages_for_job)
    app.mount("/jobs/{job_id}", review, name="review-job")

    @app.get("/failed/{job_id}", response_class=HTMLResponse)
    def failed(job_id: str) -> HTMLResponse:
        return HTMLResponse(failed_job_page(jobs_dir, job_id))

    return app
