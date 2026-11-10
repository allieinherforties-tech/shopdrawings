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
  untouched here. Approving a job runs the generation pipeline (through
  the review app's ``on_approved`` hook), which stores the .3dm model,
  DXF cut sheets, cut list, and escalation packages under ``outputs/``
  and offers them as download links in the review payload.
- ``/jobs/{job_id}/outputs`` — the fabrication-outputs page: download
  links for what generation produced, or the recorded failure, or an
  explanation when nothing has been generated yet.
- ``/jobs/{job_id}/outputs/{file}`` — one generated file as a download,
  served only when the job's generation record lists it.
- ``/failed/{job_id}`` — what failed, why, what to do.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from shop_drawings.extraction.pipeline import extract_spec, vision_report
from shop_drawings.extraction.vision import VisionClient
from shop_drawings.review.app import OutputLink, create_app
from shop_drawings.schema import FurnitureSpec
from shop_drawings.studio import jobs
from shop_drawings.studio.pages import failed_job_page, outputs_page

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
        """Run extraction and persist the spec, rendered pages, and vision record."""
        result = extract_spec(source_path, vision_client=vision_client)
        spec = result.spec
        jobs.write_spec(paths, spec.model_dump_json(indent=2) + "\n")
        jobs.write_vision(paths, vision_report(result))
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

    def _vision_for_job(request: Request) -> Path:
        return _paths_for(request).vision

    def _paths_for(request: Request) -> jobs.JobPaths:
        job_id = str(request.path_params.get("job_id", ""))
        return _job_paths(job_id)

    def _job_paths(job_id: str) -> jobs.JobPaths:
        if not job_id or "/" in job_id:
            raise HTTPException(status_code=404, detail="unknown job")
        return jobs.JobPaths(jobs_dir, job_id)

    def _generate_for_job(request: Request) -> None:
        """Approved → generated: run the pipeline, record what it produced.

        Runs synchronously — the reviewer's approve response carries the
        download links, so the demo needs no background workers.
        """
        paths = _paths_for(request)
        spec = FurnitureSpec.model_validate_json(paths.spec.read_text(encoding="utf-8"))
        try:
            record = jobs.run_generation(paths, spec)
        except Exception as exc:
            # The approval stands; the generation failure is the
            # reviewer's outcome, recorded where the outputs page and the
            # listing show it — logged here too, never swallowed.
            logger.exception("generation failed for job %s", paths.root.name)
            jobs.record_generation_failure(paths, error=f"{type(exc).__name__}: {exc}")
            return
        files = record.get("files")
        logger.info(
            "generated %d output file(s) for job %s",
            len(files) if isinstance(files, list) else 0,
            paths.root.name,
        )

    def _outputs_for(request: Request) -> list[OutputLink]:
        """Download links for a job's generated outputs, if it has any."""
        paths = _paths_for(request)
        record = jobs.read_generation_record(paths)
        if record is None or record.get("status") != jobs.GenerationStatus.GENERATED.value:
            return []
        job_id = paths.root.name
        return [
            OutputLink(label=label, url=f"/jobs/{job_id}/outputs/{quote(name)}")
            for label, name in jobs.output_labels(record)
        ]

    # One review instance serves every job: the spec and source-page
    # directories are resolved per request from the mounted job id.
    # Approval runs generation (the hook); generated outputs surface as
    # download links in the review payload.
    review = create_app(
        _spec_for_job,
        source_images_dir=_pages_for_job,
        vision_path=_vision_for_job,
        on_approved=_generate_for_job,
        outputs=_outputs_for,
    )

    # Download endpoints, registered before the review mount so they win
    # route matching under /jobs/{job_id}/.
    @app.get("/jobs/{job_id}/outputs", response_class=HTMLResponse)
    def job_outputs(job_id: str) -> HTMLResponse:
        paths = _job_paths(job_id)
        if not paths.spec.exists():
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id!r}")
        return HTMLResponse(outputs_page(paths))

    @app.get("/jobs/{job_id}/outputs/{file_path:path}", response_model=None)
    def download_output(job_id: str, file_path: str) -> FileResponse:
        paths = _job_paths(job_id)
        record = jobs.read_generation_record(paths)
        if record is None or record.get("status") != jobs.GenerationStatus.GENERATED.value:
            raise HTTPException(status_code=404, detail="no generated outputs for this job")
        # Only names the generation record lists are served — never an
        # arbitrary path under (or beyond) the job's outputs directory.
        if file_path not in {name for _, name in jobs.output_labels(record)}:
            raise HTTPException(status_code=404, detail="no such output file")
        target = paths.outputs / file_path
        if not target.is_file():
            raise HTTPException(status_code=404, detail="output file is missing")
        return FileResponse(target, filename=Path(file_path).name)

    app.mount("/jobs/{job_id}", review, name="review-job")

    @app.get("/failed/{job_id}", response_class=HTMLResponse)
    def failed(job_id: str) -> HTMLResponse:
        return HTMLResponse(failed_job_page(jobs_dir, job_id))

    return app
