from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline.orchestrator import run_pipeline  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the trend -> content -> social pipeline once.")
    parser.add_argument("--limit-per-source", type=int, default=10)
    parser.add_argument("--top-n", type=int, default=15)
    parser.add_argument(
        "--platforms",
        type=str,
        default=None,
        help="comma-separated platform names, e.g. facebook,twitter (default: TARGET_PLATFORMS from .env)",
    )
    parser.add_argument("--no-video", action="store_true", help="skip video generation, post images only")
    args = parser.parse_args()

    platforms = args.platforms.split(",") if args.platforms else None
    result = run_pipeline(
        limit_per_source=args.limit_per_source,
        top_n=args.top_n,
        platforms=platforms,
        include_video=not args.no_video,
    )
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
