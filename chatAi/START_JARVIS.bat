@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run: python -m venv .venv
  pause
  exit /b 1
)
echo JARVIS server: http://192.168.8.60:9014
echo Keep this window open while using the Android app.
".venv\Scripts\python.exe" JARVIS_AI.py
pause
