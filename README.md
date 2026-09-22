# Universal AI Research & Data Agent

This repository contains the initial Phase 1 implementation of the universal AI agent.

## Current Phase

Phase 1 includes:
- Python FastAPI backend
- Modular agent architecture
- Calculator tool
- Tool selection heuristics
- Example test coverage
- Environment configuration

## Structure

- backend/app/agent: orchestration and tool selection
- backend/app/tools: modular tool implementations
- backend/app/api: API routes and validation
- backend/tests: regression and unit tests

## Environment

Copy the example environment file and fill in your keys if you want to enable OpenAI later:

```bash
cd backend
copy .env.example .env
```

## Install

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run locally

```bash
cd backend
.venv\Scripts\activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Test

```bash
cd backend
.venv\Scripts\activate
pytest -q
```

## Sample question

- What is 25 * 48?

Expected result: the agent selects the calculator tool and returns the exact result.
