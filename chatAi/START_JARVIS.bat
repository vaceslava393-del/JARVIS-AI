@echo off
cd /d "%~dp0"

set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not exist "%PYTHON_EXE%" (
  echo Python not found. Install Python and run: pip install -r requirements.txt
  pause
  exit /b 1
)

echo JARVIS server: http://192.168.8.60:9014
echo Keep this window open while using the Android app.
"%PYTHON_EXE%" JARVIS_AI.py
pause
