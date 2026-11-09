"""Run the studio front door locally:

    python -m shop_drawings.studio --jobs-dir ./jobs --host 127.0.0.1 --port 8321

Upload a design at ``/`` and the browser lands in review for that design.
Extraction is deterministic; when ``ANTHROPIC_API_KEY`` is set, vision
assist joins extraction for scans, sketches, and part-label hints. The
deterministic path works fully without the key.

The review-only mode still runs on its own:

    python -m shop_drawings.review --spec examples/entry-cabinet-review.json
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from shop_drawings.extraction.vision import AnthropicVisionClient, VisionClient
from shop_drawings.studio.app import create_studio_app

#: Vision model used when ANTHROPIC_API_KEY is configured. The key gate is
#: the only env read here; without it the studio runs fully deterministic.
VISION_MODEL = "claude-sonnet-4-5"


def vision_client_from_env() -> VisionClient | None:
    """A vision client when a key is configured, else None."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return AnthropicVisionClient(model=VISION_MODEL)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="shop_drawings.studio", description="Shop Drawings studio front door"
    )
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=Path("jobs"),
        help="directory where uploaded designs and their jobs are kept",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8321)
    args = parser.parse_args()

    args.jobs_dir.mkdir(parents=True, exist_ok=True)

    app = create_studio_app(args.jobs_dir, vision_client=vision_client_from_env())
    print(f"Shop Drawings studio: http://{args.host}:{args.port}/")
    print(f"  jobs directory: {args.jobs_dir.resolve()}")
    if vision_client_from_env() is None:
        print("  vision assist: off (set ANTHROPIC_API_KEY to enable)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
