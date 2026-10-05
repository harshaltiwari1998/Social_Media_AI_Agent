from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.media.image_gen import generate_image  # noqa: E402
from app.media.video_gen import generate_video  # noqa: E402


def main() -> None:
    """Generate images + a short video for a hand-picked topic (no trend fetch, no LLM, no posting).

    The spec is a JSON file with: headline, caption, video_script, image_prompts (list),
    optional negative_prompt and tts_voice. Writing the narration by hand avoids the local
    LLM's habit of inventing facts.
    """
    parser = argparse.ArgumentParser(description="Make images + a short video for a custom topic.")
    parser.add_argument("spec", type=Path, help="path to a JSON content spec")
    parser.add_argument("--no-video", action="store_true", help="only generate images")
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if spec.get("tts_voice"):
        settings.tts_voice = spec["tts_voice"]

    image_kwargs = {"negative_prompt": spec["negative_prompt"]} if spec.get("negative_prompt") else {}
    images = [generate_image(prompt, **image_kwargs) for prompt in spec["image_prompts"]]
    image_paths = [r["data"]["path"] for r in images if r["success"]]

    result = {"images": images, "video": None}
    if image_paths and not args.no_video:
        result["video"] = generate_video(
            image_paths, spec["caption"], spec["video_script"], headline=spec.get("headline")
        )

    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if image_paths and (args.no_video or (result["video"] or {}).get("success")) else 1)


if __name__ == "__main__":
    main()
