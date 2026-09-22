from __future__ import annotations

from typing import Any

from app.agent.tool_selector import ToolSelector
from app.tools.calculator import CalculatorTool


class Agent:
    """Simple orchestration layer for the Phase 1 implementation."""

    def __init__(self) -> None:
        self.tool_selector = ToolSelector()
        self.tools = {
            "calculator": CalculatorTool(),
        }

    def process_question(self, question: str) -> dict[str, Any]:
        selected_tools = self.tool_selector.select_tools(question)
        tool_results: list[dict[str, Any]] = []

        for tool_choice in selected_tools:
            tool_name = tool_choice["tool"]
            if tool_name not in self.tools:
                tool_results.append({
                    "tool": tool_name,
                    "success": False,
                    "error": "Tool not available in this phase",
                    "source": tool_name,
                })
                continue

            tool = self.tools[tool_name]
            if tool_name == "calculator":
                # Simple extraction for Phase 1: handle the core numeric expression in the question.
                expression = self._extract_expression(question)
                result = tool.execute({"expression": expression})
            else:
                result = {"success": False, "source": tool_name, "error": "Not implemented in Phase 1"}

            tool_results.append({
                "tool": tool_name,
                **result,
            })

        answer = self._build_answer(question, tool_results)
        return {
            "success": True,
            "question": question,
            "selected_tools": selected_tools,
            "tool_results": tool_results,
            "answer": answer,
        }

    def _extract_expression(self, question: str) -> str:
        match = self._first_numeric_expression(question)
        if match:
            return match
        return "0"

    def _first_numeric_expression(self, question: str) -> str | None:
        # A minimal parser for "25 * 48" / "what is 25*48" style requests.
        import re

        pattern = r"(-?\d+(?:\.\d+)?\s*(?:[+\-*/%]\s*-?\d+(?:\.\d+)?)*)"
        match = re.search(pattern, question)
        if not match:
            return None
        expression = match.group(0).replace("%", "/100")
        expression = re.sub(r"\s+", " ", expression)
        return expression

    def _build_answer(self, question: str, tool_results: list[dict[str, Any]]) -> str:
        if not tool_results:
            return "I could not determine which tool to use for that request."

        calculator_result = next((result for result in tool_results if result.get("tool") == "calculator" and result.get("success")), None)
        if calculator_result:
            expression = calculator_result.get("data", {}).get("expression")
            value = calculator_result.get("data", {}).get("result")
            return f"The result is {value} for {expression}."

        return "I processed the request, but no supported tool could produce a verified result in this phase."
