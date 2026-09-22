from __future__ import annotations

import ast
import operator
from typing import Any

from app.tools.base import BaseTool


_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


class CalculatorTool(BaseTool):
    name = "calculator"
    description = "Performs arithmetic, percentages, averages, and simple numeric calculations safely."

    def validate(self, inputs: dict[str, Any]) -> dict[str, Any]:
        expression = inputs.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError("Expression must be a non-empty string.")
        return {"expression": expression.strip()}

    def _evaluate(self, node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPERATORS:
            left = self._evaluate(node.left)
            right = self._evaluate(node.right)
            return float(_ALLOWED_OPERATORS[type(node.op)](left, right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPERATORS:
            operand = self._evaluate(node.operand)
            return float(_ALLOWED_OPERATORS[type(node.op)](operand))
        raise ValueError(f"Unsupported expression: {ast.dump(node)}")

    def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        try:
            validated = self.validate(inputs)
            parsed = ast.parse(validated["expression"], mode="eval")
            result = self._evaluate(parsed.body)
            normalized = result
            if normalized.is_integer():
                normalized = int(normalized)
            return {
                "success": True,
                "source": "Calculator",
                "data": {"expression": validated["expression"], "result": normalized},
                "metadata": {"tool": self.name},
            }
        except Exception as exc:  # pragma: no cover - defensive
            return {
                "success": False,
                "source": "Calculator",
                "error": str(exc),
                "data": None,
                "metadata": {"tool": self.name},
            }
