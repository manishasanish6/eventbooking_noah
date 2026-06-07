@echo off
cd /d "D:\Passion\Booking"
.\venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
pause