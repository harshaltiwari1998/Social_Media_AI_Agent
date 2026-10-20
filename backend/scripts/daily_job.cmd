@echo off
rem Entry point for the "AI Trend Poster Daily" Windows scheduled task.
cd /d "%~dp0.."
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" scripts\daily_job.py %*
rem Free AI-motion videos (Hugging Face ZeroGPU quota) - see scripts/motion_daily.py
".venv\Scripts\python.exe" scripts\motion_daily.py
rem Gameplay Shorts/highlights from recordings in VIDEOS GAMING - see scripts/gameplay_daily.py
".venv\Scripts\python.exe" scripts\gameplay_daily.py
