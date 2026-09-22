from app.agent.agent import Agent
from app.tools.calculator import CalculatorTool
from app.agent.tool_selector import ToolSelector


def test_calculator_handles_basic_math():
    tool = CalculatorTool()
    result = tool.execute({"expression": "25 * 48"})

    assert result["success"] is True
    assert result["data"]["result"] == 1200
    assert result["source"] == "Calculator"


def test_tool_selector_routes_math_questions():
    selector = ToolSelector()
    plan = selector.select_tools("What is 25 * 48?")

    assert plan[0]["tool"] == "calculator"
    assert plan[0]["reason"] == "Mathematical calculation requested"


def test_agent_processes_math_question():
    agent = Agent()
    response = agent.process_question("What is 25 * 48?")

    assert response["success"] is True
    assert response["tool_results"][0]["tool"] == "calculator"
    assert response["answer"].startswith("The result is")
    assert "1200" in response["answer"]
