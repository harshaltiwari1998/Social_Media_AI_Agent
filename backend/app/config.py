from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Keep all model/library downloads inside the project (on the F: drive) instead
# of the default C:\Users\...\.cache — must run before torch/huggingface_hub
# are imported anywhere, so this lives at the top of the first-imported module.
_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
os.environ.setdefault("HF_HOME", str(_CACHE_DIR / "huggingface"))
os.environ.setdefault("TRANSFORMERS_CACHE", str(_CACHE_DIR / "huggingface" / "transformers"))
os.environ.setdefault("TORCH_HOME", str(_CACHE_DIR / "torch"))
os.environ.setdefault("XDG_CACHE_HOME", str(_CACHE_DIR / "xdg"))


class Settings(BaseSettings):
    app_env: str = Field(default="development", alias="APP_ENV")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:3000"], alias="CORS_ORIGINS")

    # Safety: keep posting disabled until explicitly turned off after reviewing output.
    dry_run: bool = Field(default=True, alias="DRY_RUN")

    # Trend sources
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")
    reddit_client_id: str | None = Field(default=None, alias="REDDIT_CLIENT_ID")
    reddit_client_secret: str | None = Field(default=None, alias="REDDIT_CLIENT_SECRET")
    reddit_user_agent: str = Field(default="universal-ai-agent/0.1 trend-fetcher", alias="REDDIT_USER_AGENT")
    twitter_bearer_token: str | None = Field(default=None, alias="TWITTER_BEARER_TOKEN")

    # Social posting (Stage 4)
    meta_page_id: str | None = Field(default=None, alias="META_PAGE_ID")
    meta_ig_user_id: str | None = Field(default=None, alias="META_IG_USER_ID")
    meta_access_token: str | None = Field(default=None, alias="META_ACCESS_TOKEN")
    twitter_api_key: str | None = Field(default=None, alias="TWITTER_API_KEY")
    twitter_api_secret: str | None = Field(default=None, alias="TWITTER_API_SECRET")
    twitter_access_token: str | None = Field(default=None, alias="TWITTER_ACCESS_TOKEN")
    twitter_access_secret: str | None = Field(default=None, alias="TWITTER_ACCESS_SECRET")
    youtube_client_secret_file: str | None = Field(default=None, alias="YOUTUBE_CLIENT_SECRET_FILE")

    # Instagram's Content Publishing API has no raw-file upload endpoint - it
    # only accepts a publicly reachable image_url/video_url. If this is set to
    # whatever serves MEDIA_OUTPUT_DIR publicly, it's used as-is; otherwise each
    # post opens a temporary Cloudflare quick tunnel via CLOUDFLARED_PATH.
    media_public_base_url: str | None = Field(default=None, alias="MEDIA_PUBLIC_BASE_URL")
    cloudflared_path: str = Field(default="cloudflared", alias="CLOUDFLARED_PATH")

    # Stage 5: which platforms the orchestrator targets by default (non-gaming content).
    target_platforms: List[str] = Field(
        default_factory=lambda: ["instagram", "facebook"], alias="TARGET_PLATFORMS"
    )
    # Gaming content goes only to the gaming YouTube channel (not Instagram/Facebook).
    gaming_platforms: List[str] = Field(
        default_factory=lambda: ["youtube"], alias="GAMING_PLATFORMS"
    )
    # Movie content (new releases, reviews) goes to these platforms.
    movie_platforms: List[str] = Field(
        default_factory=lambda: ["youtube_movies", "instagram", "facebook"], alias="MOVIE_PLATFORMS"
    )
    # Two YouTube channels, one login (token file) each: "youtube" is the gaming channel
    # (category 20 = Gaming), "youtube_movies" the movies channel (24 = Entertainment).
    youtube_token_file: str = Field(default=".cache/youtube_token.json", alias="YOUTUBE_TOKEN_FILE")
    youtube_category_id: str = Field(default="20", alias="YOUTUBE_CATEGORY_ID")
    youtube_movies_token_file: str = Field(default=".cache/youtube_token_movies.json", alias="YOUTUBE_MOVIES_TOKEN_FILE")
    youtube_movies_category_id: str = Field(default="24", alias="YOUTUBE_MOVIES_CATEGORY_ID")

    # Analysis LLM backend: "ollama" (free, local, default) or "openai"
    llm_backend: str = Field(default="ollama", alias="LLM_BACKEND")
    ollama_host: str = Field(default="http://localhost:11434", alias="OLLAMA_HOST")
    ollama_model: str = Field(default="llama3.2:3b", alias="OLLAMA_MODEL")

    # Media generation (Stage 2/3)
    sd_model_id: str = Field(default="stable-diffusion-v1-5/stable-diffusion-v1-5", alias="SD_MODEL_ID")
    # Defaults tuned for a 4GB VRAM laptop GPU: a 768/x4-upscale combo measured ~58
    # min/image here (VAE decode of a 3072x3072 image OOMs without tiling, and even
    # tiled it's too slow for a pipeline that generates several images per run), so
    # upscaling is off by default - flip SD_ENABLE_UPSCALE=true for one-off hi-res runs.
    sd_base_size: int = Field(default=768, alias="SD_BASE_SIZE")
    sd_enable_upscale: bool = Field(default=False, alias="SD_ENABLE_UPSCALE")
    sd_upscaler_model_id: str = Field(
        default="stabilityai/stable-diffusion-x4-upscaler", alias="SD_UPSCALER_MODEL_ID"
    )
    media_output_dir: str = Field(default="media_output", alias="MEDIA_OUTPUT_DIR")
    tts_voice: str = Field(default="hi-IN-SwaraNeural", alias="TTS_VOICE")
    # Gaming videos (HarryBeast Gaming) are narrated in English; everything else uses TTS_VOICE.
    gaming_tts_voice: str = Field(default="en-US-GuyNeural", alias="GAMING_TTS_VOICE")
    # Narration speed passed to edge-tts; specs can override it (e.g. slower for tributes).
    tts_rate: str = Field(default="+5%", alias="TTS_RATE")
    # Optional royalty-free background music (mp3/wav), looped and ducked under the narration.
    bgm_path: str | None = Field(default=None, alias="BGM_PATH")

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
