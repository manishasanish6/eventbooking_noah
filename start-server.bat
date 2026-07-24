@echo off
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
pause