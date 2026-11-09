"""Run the review gate UI locally:

    python -m shop_drawings.review --spec examples/entry-cabinet-review.json

Optional flags:

    --host / --port          bind address (default 127.0.0.1:8321)
    --source-images DIR      directory of rendered PDF pages named
                             ``page-1.png`` (or .jpg/.jpeg/.webp); when
                             given, crops show the real source pages
                             instead of placeholder renderings.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from shop_drawings.review.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="shop_drawings.review", description="Shop Drawings review gate UI"
    )
    parser.add_argument(
        "--spec",
        type=Path,
        required=True,
        help="path to the furniture spec JSON to review",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8321)
    parser.add_argument(
        "--source-images",
        type=Path,
        default=None,
        help="directory of rendered source pages (page-1.png, ...)",
    )
    args = parser.parse_args()

    if not args.spec.exists():
        raise SystemExit(f"spec file not found: {args.spec}")
    if args.source_images is not None and not args.source_images.is_dir():
        raise SystemExit(f"source-images directory not found: {args.source_images}")

    app = create_app(args.spec, source_images_dir=args.source_images)
    print(f"Review gate: http://{args.host}:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
