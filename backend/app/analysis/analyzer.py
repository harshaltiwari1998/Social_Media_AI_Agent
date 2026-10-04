from __future__ import annotations

import json
import re
from typing import Any

from app.config import settings

_SYSTEM_PROMPT = """You are a social media content strategist covering three beats:
(1) entertainment - Hollywood, Bollywood and Tollywood movies, video games and gaming news, and
songs/music from around the world; (2) sports - cricket, football and other major matches,
tournaments and players; and (3) AI - new AI models, tools and open-source AI projects.
You will be given a ranked list of currently trending topics pulled from Google Trends (India daily
searches, which span every category), Reddit, X, Hugging Face and GitHub.

Ignore trends that fit none of these beats (e.g. politics, weather, stock prices, exam results)
even if they score highly. This content is posted publicly on a brand page, so also skip anything
involving wars or conflicts, political or religious messaging, protests, crime, arrests, prisons,
deaths, or adult/"uncensored" content - even when it is sports or celebrity related.

Keep proper nouns (team, player, film, game, celebrity, company and AI model names) in their
original English spelling inside the Hindi text - e.g. "Konyaspor", "Qwen", "Deadlock" - never
transliterate them into Devanagari. When enough relevant trends exist, mix the beats - don't make all
ideas about the same beat.

Write "headline", "caption", and "video_script" in Hindi (Devanagari script) - the target audience
is Hindi-speaking. Keep "image_prompts" and "tags" in English (image generation and YouTube tags
work best in English regardless of the audience's language).

Respond with ONLY a JSON object matching this shape:
{
  "summary": "2-3 sentence plain-language overview of what's hot right now and why (Hindi)",
  "content_ideas": [
    {
      "trend_title": "must match one of the input trend titles",
      "headline": "short, scroll-stopping headline for a social post (Hindi)",
      "caption": "1-2 sentence caption/body copy for the post, written for a general (non-technical) audience (Hindi)",
      "image_prompts": [
        "detailed Stable Diffusion prompt for scene 1, matching the first beat of video_script (English)",
        "detailed Stable Diffusion prompt for scene 2, matching the next beat (English)",
        "detailed Stable Diffusion prompt for scene 3, matching the final beat (English)"
      ],
      "video_script": "a 60-90 word narration script (5-7 sentences, about 25-35 seconds spoken) about this trend: a hook, the key facts from the trend's summary, and a question for viewers at the end (Hindi)",
      "tags": ["5-8 relevant single/short-phrase keywords for YouTube video tags, e.g. Bollywood, Gaming, Cricket, AI, the trend's own name (English)"]
    }
  ]
}
Produce exactly 3-4 image_prompts per idea, one per beat of that idea's video_script, each visually
distinct (different framing/subject/angle) so the resulting video is a scene-changing slideshow
rather than one static image held for the whole narration. Image generation cannot render legible
text, so image_prompts must never ask for screens/dashboards/UI showing readable text, charts with
labels, code, or any words - describe purely visual concepts instead (abstract tech visuals, robots,
glowing networks, data particles, devices shown from an angle with no visible screen content, etc).
Each idea must be about exactly one input trend: copy its title verbatim into "trend_title", and
base the headline, caption and script only on that trend's own title and summary - never compare
it to a different topic or sport, and never invent facts that aren't in the summary.
Produce exactly 3 content_ideas, chosen from the highest-scoring, most newsworthy relevant trends."""


def analyze(trends: list[dict[str, Any]]) -> dict[str, Any]:
    """Send aggregated trends to the configured LLM backend and get back content ideas."""
    if not trends:
        return {"success": False, "error": "No trends supplied to analyze.", "data": None}

    trend_summary = [
        {"title": t["title"], "source": t["source"], "score": t["score"], "summary": t.get("summary")}
        for t in trends
    ]

    if settings.llm_backend == "ollama":
        result = _analyze_with_ollama(trend_summary)
    else:
        result = _analyze_with_openai(trend_summary)
    if result["success"]:
        result["data"] = _clean_ideas(result["data"])
    return result


_LANG_MARKER = re.compile(r"\s*\((?:Hindi|English)\)\s*$", re.IGNORECASE)


def _strip_marker(value: Any) -> Any:
    """Small models echo the schema's '(Hindi)'/'(English)' hints into their output - remove them."""
    if isinstance(value, str):
        return _LANG_MARKER.sub("", value).strip()
    if isinstance(value, list):
        return [_strip_marker(v) for v in value]
    return value


def _clean_ideas(data: dict[str, Any]) -> dict[str, Any]:
    """Strip echoed hints and drop malformed ideas (e.g. plain strings instead of objects)."""
    required = {"trend_title", "headline", "caption", "image_prompts", "video_script"}
    ideas = [
        {key: _strip_marker(val) for key, val in idea.items()}
        for idea in data.get("content_ideas") or []
        if isinstance(idea, dict) and required <= idea.keys() and isinstance(idea["image_prompts"], list)
    ]
    return {**data, "summary": _strip_marker(data.get("summary")), "content_ideas": ideas}


def _analyze_with_ollama(trend_summary: list[dict[str, Any]]) -> dict[str, Any]:
    import httpx

    try:
        response = httpx.post(
            f"{settings.ollama_host}/api/chat",
            json={
                "model": settings.ollama_model,
                "format": "json",
                "stream": False,
                # Ollama defaults to a 2048-token context, which silently truncates this
                # system prompt + the trend list and makes the model produce generic junk.
                "options": {"num_ctx": 8192, "temperature": 0.6},
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
