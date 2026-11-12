@echo off
rem Entry point for the "Pokemon Evolution Daily" Windows scheduled task (20:15 IST, right after the HF ZeroGPU
rem quota resets at ~19:57 IST): up to 3 evolution shorts, posted and merged into gaming-state.
cd /d "%~dp0.."
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
".venv\Scripts\python.exe" scripts\laptop_batch.py evo --evolution --max-shorts 3 >> logs\evolution_laptop.log 2>&1
