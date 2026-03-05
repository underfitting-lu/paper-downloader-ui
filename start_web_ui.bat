@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [SETUP] Creating virtual environment...
  where py >nul 2>nul
  if %errorlevel%==0 (
    py -3 -m venv .venv
  ) else (
    python -m venv .venv
  )
)

call ".venv\Scripts\activate"

echo [SETUP] Installing dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Dependency installation failed.
  pause
  exit /b 1
)

echo [RUN] Starting local web UI...
python web_ui.py

endlocal
