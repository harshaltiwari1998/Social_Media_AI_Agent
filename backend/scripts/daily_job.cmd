@echo off
rem Entry point for the "AI Trend Poster Daily" Windows scheduled task.
cd /d "%~dp0.."
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" scripts\daily_job.py %*
