from __future__ import annotations

import re
from typing import Any


class ToolSelector:
    """Selects likely tools for a user request using simple deterministic rules."""

    def select_tools(self, question: str) -> list[dict[str, str]]:
        normalized = question.strip().lower()
        selected: list[dict[str, str]] = []

        if re.search(r"\d|\+|\-|\*|/|percent|average|sum|difference|ratio|growth", normalized):
            selected.append({"tool": "calculator", "reason": "Mathematical calculation requested"})

        if selected:
            return selected

        if re.search(r"(azure devops|devops|work item|bug|sprint|p1|p2|p3)", normalized):
            selected.append({"tool": "azure_devops", "reason": "Project tracking data requested"})

        if re.search(r"(jira|issue|story|epic|sprint)", normalized):
            selected.append({"tool": "jira", "reason": "Issue tracking data requested"})

        if re.search(r"(github|repo|pull request|commit|issue)", normalized):
            selected.append({"tool": "github", "reason": "Repository source requested"})

        if re.search(r"(pdf|docx|excel|csv|document|report|according to our project|in the document)", normalized):
            selected.append({"tool": "document_search", "reason": "Document evidence requested"})

        if not selected:
            selected.append({"tool": "general_qa", "reason": "No specialized tool matched; general reasoning likely needed"})

        return selected
