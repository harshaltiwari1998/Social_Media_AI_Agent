from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class TrendItem(BaseModel):
    """A single normalized trend signal from any source."""

    id: str
    source: str
    title: str
    url: str | None = None
    score: float = 0.0
    summary: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TrendFetchError(RuntimeError):
    """Raised by a fetcher when a source is unavailable or unconfigured."""

    def __init__(self, source: str, reason: str) -> None:
        self.source = source
        self.reason = reason
        super().__init__(f"[{source}] {reason}")
