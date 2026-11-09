"""The review-gate web application.

Endpoints serve the review queue (blueprint §4 priority order), apply
reviewer corrections through the ``ReviewStore`` (which enforces the gate
through the schema), and render traceability crops. The gate itself lives
in ``shop_drawings.schema`` — this layer only surfaces it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.requests import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from shop_drawings.review.conflicts import detect_conflicts
from shop_drawings.review.models import (
    LogEntry,
    ReviewQueue,
    ValueAddress,
    build_queue,
    spec_payload,
)
from shop_drawings.review.pageart import page_svg
from shop_drawings.review.store import GateError, ReviewStore
from shop_drawings.schema import FurnitureSpec, ReviewStatus

STATIC_DIR = Path(__file__).parent / "static"

_PAGE_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


class CorrectionRequest(BaseModel):
    """Retype a value and/or set its review status; at least one is required."""

    model_config = ConfigDict(extra="forbid")

    address: ValueAddress
    value: float | None = Field(default=None, ge=0)
    status: ReviewStatus | None = None


class BatchConfirmRequest(BaseModel):
    """Confirm values, parts, and constraints in one action."""

    model_config = ConfigDict(extra="forbid")

    addresses: list[ValueAddress] = Field(default_factory=list)
    part_ids: list[str] = Field(default_factory=list)
    constraint_indexes: list[int] = Field(default_factory=list)


class PartStatusRequest(BaseModel):
    """Set a part's review status."""

    model_config = ConfigDict(extra="forbid")

    part_id: str
    status: ReviewStatus


class ConstraintStatusRequest(BaseModel):
    """Set an assembly constraint's review status."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    status: ReviewStatus


class ResolveQuestionRequest(BaseModel):
    """A reviewer's plain-language answer to a blocked question."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    answer: str = Field(min_length=1)


class ReviewPayload(BaseModel):
    """Everything the UI needs to render one review session."""

    spec: dict[str, Any]
    queue: ReviewQueue
    gate_errors: list[str]
    spec_status: ReviewStatus
    log: list[LogEntry]


def _find_page_image(source_images_dir: Path | None, page: int) -> Path | None:
    if source_images_dir is None:
        return None
    for suffix in _PAGE_IMAGE_SUFFIXES:
        candidate = source_images_dir / f"page-{page}{suffix}"
        if candidate.exists():
            return candidate
    return None


def create_app(
    spec_path: Path,
    *,
    source_images_dir: Path | None = None,
) -> FastAPI:
    """Build the review app against one spec file."""
    store = ReviewStore(spec_path)

    app = FastAPI(title="Shop Drawings — Review Gate", docs_url=None, redoc_url=None)

    @app.exception_handler(GateError)
    async def _gate_error(_: Request, exc: GateError) -> JSONResponse:
        # 409: the request is valid but the review gate blocks it — the UI
        # shows these errors verbatim instead of a generic failure.
        return JSONResponse(status_code=409, content={"gate_errors": exc.errors})

    def _run(action: Callable[[], FurnitureSpec]) -> ReviewPayload:
        """Apply a reviewer action and return the refreshed review payload."""
        try:
            action()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown part: {exc}") from exc
        except IndexError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return get_review()

    @app.get("/api/review", response_model=None)
    def get_review() -> ReviewPayload:
        spec = store.reread()
        return ReviewPayload(
            spec=spec_payload(spec),
            queue=build_queue(spec, detect_conflicts(spec)),
            gate_errors=spec.approval_errors(),
            spec_status=spec.status,
            log=store.read_log(),
        )

    @app.post("/api/values", response_model=None)
    def correct_value(request: CorrectionRequest) -> ReviewPayload:
        if request.value is None and request.status is None:
            raise HTTPException(status_code=422, detail="a value or a status is required")

        def apply() -> FurnitureSpec:
            return store.correct_value(request.address, value=request.value, status=request.status)

        return _run(apply)

    @app.post("/api/values/confirm-batch", response_model=None)
    def confirm_batch(request: BatchConfirmRequest) -> ReviewPayload:
        def apply() -> FurnitureSpec:
            for address in request.addresses:
                store.correct_value(address, status=ReviewStatus.APPROVED)
            for part_id in request.part_ids:
                store.set_part_status(part_id, ReviewStatus.APPROVED)
            for index in request.constraint_indexes:
                store.set_constraint_status(index, ReviewStatus.APPROVED)
            return store.spec

        return _run(apply)

    @app.post("/api/parts", response_model=None)
    def set_part_status(request: PartStatusRequest) -> ReviewPayload:
        return _run(lambda: store.set_part_status(request.part_id, request.status))

    @app.post("/api/constraints", response_model=None)
    def set_constraint_status(request: ConstraintStatusRequest) -> ReviewPayload:
        return _run(lambda: store.set_constraint_status(request.index, request.status))

    @app.post("/api/questions/resolve", response_model=None)
    def resolve_question(request: ResolveQuestionRequest) -> ReviewPayload:
        return _run(lambda: store.resolve_question(request.index, request.answer))

    @app.post("/api/approve", response_model=None)
    def approve() -> ReviewPayload:
        return _run(store.approve)

    @app.get("/api/log", response_model=None)
    def get_log() -> list[LogEntry]:
        return store.read_log()

    @app.get("/api/page/{page}/image")
    def page_image(page: int) -> Response:
        image = _find_page_image(source_images_dir, page)
        if image is not None:
            return FileResponse(image)
        return Response(content=page_svg(store.spec, page), media_type="image/svg+xml")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
