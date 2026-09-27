from __future__ import annotations

import json
from typing import Any

from app.config import settings

_SYSTEM_PROMPT = """You are a social media content strategist specializing in AI/ML news.
You will be given a ranked list of currently trending AI models, tools, and repos
pulled from Hugging Face, GitHub, Google Trends, Reddit, and X.

Respond with ONLY a JSON object matching this shape:
{
  "summary": "2-3 sentence plain-language overview of what's hot right now and why",
  "content_ideas": [
    {
      "trend_title": "must match one of the input trend titles",
      "headline": "short, scroll-stopping headline for a social post",
      "caption": "1-2 sentence caption/body copy for the post, written for a general tech audience",
      "image_prompts": [
        "detailed Stable Diffusion prompt for scene 1, matching the first beat of video_script",
        "detailed Stable Diffusion prompt for scene 2, matching the next beat",
        "detailed Stable Diffusion prompt for scene 3, matching the final beat"
      ],
      "video_script": "a 3-4 sentence narration script for a 20-30 second short-form video about this trend"
    }
  ]
}
Produce exactly 3-4 image_prompts per idea, one per beat of that idea's video_script, each visually
distinct (different framing/subject/angle) so the resulting video is a scene-changing slideshow
rather than one static image held for the whole narration. Produce exactly 3 content_ideas, chosen
from the highest-scoring, most newsworthy trends."""


def analyze(trends: list[dict[str, Any]]) -> dict[str, Any]:
    """Send aggregated trends to the configured LLM backend and get back content ideas."""
    if not trends:
        return {"success": False, "error": "No trends supplied to analyze.", "data": None}

    trend_summary = [
        {"title": t["title"], "source": t["source"], "score": t["score"], "summary": t.get("summary")}
        for t in trends
    ]

    if settings.llm_backend == "ollama":
        return _analyze_with_ollama(trend_summary)
    return _analyze_with_openai(trend_summary)


def _analyze_with_ollama(trend_summary: list[dict[str, Any]]) -> dict[str, Any]:
    import httpx

    try:
        response = httpx.post(
            f"{settings.ollama_host}/api/chat",
            json={
                "model": settings.ollama_model,
                "format": "json",
                "stream": False,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(trend_summary, ensure_ascii=False)},
                ],
            },
            timeout=180.0,
        )
        response.raise_for_status()
        raw_content = response.json()["message"]["content"]
        parsed = json.loads(raw_content)
    except httpx.ConnectError as exc:
        return {
            "success": False,
            "error": f"could not reach Ollama at {settings.ollama_host} (is it running? try 'ollama serve'): {exc}",
            "data": None,
        }
    except Exception as exc:
        return {"success": False, "error": f"analysis failed: {exc}", "data": None}

    return {"success": True, "error": None, "data": parsed}


def _analyze_with_openai(trend_summary: list[dict[str, Any]]) -> dict[str, Any]:
    if not settings.openai_api_key:
        return {"success": False, "error": "OPENAI_API_KEY is not configured; cannot analyze trends.", "data": None}

    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - import guard
        return {"success": False, "error": f"openai package not installed: {exc}", "data": None}

    client = OpenAI(api_key=settings.openai_api_key)
    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(trend_summary, ensure_ascii=False)},
            ],
        )
        raw_content = response.choices[0].message.content or "{}"
        parsed = json.loads(raw_content)
    except Exception as exc:
        return {"success": False, "error": f"analysis failed: {exc}", "data": None}

    return {"success": True, "error": None, "data": parsed}
