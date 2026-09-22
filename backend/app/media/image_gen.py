from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from app.config import settings

_pipeline = None  # lazy-loaded singleton; loading the model takes several seconds


def _slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len] or "image"


def _get_pipeline():
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    import torch
    from diffusers import StableDiffusionPipeline

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    pipe = StableDiffusionPipeline.from_pretrained(settings.sd_model_id, torch_dtype=dtype, safety_checker=None)
    pipe = pipe.to(device)

    if device == "cuda":
        # Needed to fit Stable Diffusion 1.5 on ~4GB VRAM cards.
        pipe.enable_attention_slicing()
        pipe.enable_vae_slicing()

    _pipeline = pipe
    return _pipeline


def generate_image(
    prompt: str,
    negative_prompt: str = "blurry, low quality, distorted, watermark, text",
    steps: int = 25,
    guidance_scale: float = 7.5,
) -> dict[str, Any]:
    """Generate a single image locally via Stable Diffusion and save it to disk."""
    try:
        pipe = _get_pipeline()
    except Exception as exc:
        return {
            "success": False,
            "source": "image_gen",
            "error": f"failed to load Stable Diffusion pipeline: {exc}",
            "data": None,
        }

    try:
        result = pipe(
            prompt=prompt,
            negative_prompt=negative_prompt,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
        )
        image = result.images[0]
    except Exception as exc:
        return {
            "success": False,
            "source": "image_gen",
            "error": f"generation failed: {exc}",
            "data": None,
        }

    output_dir = Path(settings.media_output_dir) / "images"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_slugify(prompt)}-{uuid.uuid4().hex[:8]}.png"
    output_path = output_dir / filename
    image.save(output_path)

    return {
        "success": True,
        "source": "image_gen",
        "error": None,
        "data": {"path": str(output_path.resolve()), "prompt": prompt},
    }
