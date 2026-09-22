from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agent.agent import Agent
from app.analysis.analyzer import analyze
from app.pipeline.orchestrator import run_pipeline
from app.trends.aggregator import aggregate

router = APIRouter(prefix="/api")
agent = Agent()


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)


class AnalyzeRequest(BaseModel):
    limit_per_source: int = Field(default=10, ge=1, le=50)
    top_n: int = Field(default=15, ge=1, le=50)


class ToolTestRequest(BaseModel):
    tool: str
    inputs: dict[str, Any] = Field(default_factory=dict)


class PipelineRunRequest(BaseModel):
    limit_per_source: int = Field(default=10, ge=1, le=50)
    top_n: int = Field(default=15, ge=1, le=50)
    platforms: list[str] | None = None
    include_video: bool = True


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    try:
        result = agent.process_question(request.question)
        return result
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/tools/test")
def tool_test(request: ToolTestRequest) -> dict[str, Any]:
    tool = agent.tools.get(request.tool)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Tool '{request.tool}' is not available in Phase 1.")

    try:
        return tool.execute(request.inputs)
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/trends")
def trends(limit_per_source: int = 10, top_n: int = 15) -> dict[str, Any]:
    return aggregate(limit_per_source=limit_per_source, top_n=top_n)


@router.post("/analyze")
def analyze_trends(request: AnalyzeRequest) -> dict[str, Any]:
    aggregated = aggregate(limit_per_source=request.limit_per_source, top_n=request.top_n)
    result = analyze(aggregated["trends"])
    return {**result, "trends_used": aggregated["trends"], "sources_skipped": aggregated["sources_skipped"]}


@router.post("/pipeline/run")
def pipeline_run(request: PipelineRunRequest) -> dict[str, Any]:
    try:
        return run_pipeline(
            limit_per_source=request.limit_per_source,
            top_n=request.top_n,
            platforms=request.platforms,
            include_video=request.include_video,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
