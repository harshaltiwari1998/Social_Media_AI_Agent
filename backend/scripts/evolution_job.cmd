@echo off
rem Entry point for the "Pokemon Evolution Daily" Windows scheduled task (20:15 IST, right after the HF ZeroGPU
rem quota resets at ~19:57 IST): builds up to 3 evolution shorts but posts NOTHING - owner 2026-11-14: "kabhi kabhi
rem pokemon evolution bohot bakwas hote he, mujhe puch ke hi karna". The new ones open in VLC; approved lines are
rem posted with: laptop_batch.py evo --evolution --max-shorts 0 --post-approved <ids>
cd /d "%~dp0.."
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
".venv\Scripts\python.exe" scripts\laptop_batch.py evo --evolution --max-shorts 3 --review >> logs\evolution_laptop.log 2>&1
".venv\Scripts\python.exe" scripts\evolution_review.py >> logs\evolution_laptop.log 2>&1
