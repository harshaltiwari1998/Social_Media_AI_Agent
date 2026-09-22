from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """Base interface for all tools supported by the agent."""

    name: str
    description: str

    @abstractmethod
    def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the tool with validated input and return structured output."""
        raise NotImplementedError

    def validate(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return inputs
