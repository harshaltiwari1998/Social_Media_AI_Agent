from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_pipeline = None  # lazy-loaded singleton; loading the model takes several seconds
_upscale_pipeline = None  # lazy-loaded singleton for the x4 upscaler


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
    pipe.enable_attention_slicing()
    pipe.enable_vae_slicing()

    if device == "cuda":
        # Offloads unused submodules to CPU RAM between steps - needed to fit
        # Stable Diffusion 1.5 (and the upscaler alongside it) on ~4GB VRAM cards.
        pipe.enable_model_cpu_offload()
    else:
        pipe = pipe.to(device)

    _pipeline = pipe
    return _pipeline


def _get_upscale_pipeline():
    global _upscale_pipeline
    if _upscale_pipeline is not None:
        return _upscale_pipeline

    import torch
    from diffusers import StableDiffusionUpscalePipeline

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    pipe = StableDiffusionUpscalePipeline.from_pretrained(settings.sd_upscaler_model_id, torch_dtype=dtype)
    pipe.enable_attention_slicing()
    pipe.enable_vae_slicing()
    # vae_slicing only batches over multiple images; the final decode of a single
    # 3072x3072 image still OOMs at ~9GB without spatial tiling on a 4GB card.
    pipe.enable_vae_tiling()

    if device == "cuda":
        pipe.enable_model_cpu_offload()
    else:
        pipe = pipe.to(device)

    _upscale_pipeline = pipe
    return _upscale_pipeline


def _upscale(image, prompt: str):
    """Run the image through the x4 upscaler. Returns the base image unchanged on failure
    (e.g. CUDA out-of-memory on this GPU) rather than failing the whole generation.

    Default is 75 steps, which measured ~161-187s/step on this RTX 3050 (4GB, with
    model_cpu_offload) for a 768->3072 upscale - over 3 hours per image. 20 steps keeps
    the same 768->3072 output size within roughly an hour while still denoising enough
    to look meaningfully sharper than a plain resize.
    """
    try:
        pipe = _get_upscale_pipeline()
        return pipe(prompt=prompt, image=image, noise_level=20, num_inference_steps=20).images[0]
    except Exception:
        logger.exception("Upscale failed, falling back to the un-upscaled base image")
        return image


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
            height=settings.sd_base_size,
            width=settings.sd_base_size,
        )
        image = result.images[0]
    except Exception as exc:
        return {
            "success": False,
            "source": "image_gen",
            "error": f"generation failed: {exc}",
            "data": None,
        }

    if settings.sd_enable_upscale:
        image = _upscale(image, prompt)

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
