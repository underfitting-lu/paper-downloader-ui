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

echo [SETUP] Installing build dependencies...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
  echo [ERROR] Failed to install build dependencies.
  pause
  exit /b 1
)

echo [BUILD] Building standalone exe...
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --name PaperDownloaderUI ^
  --collect-all flask ^
  --collect-all werkzeug ^
  --collect-all jinja2 ^
  --collect-all click ^
  --collect-all dotenv ^
  --collect-all selenium ^
  web_ui.py

if errorlevel 1 (
  echo [ERROR] Build failed.
  pause
  exit /b 1
)

if not exist "release" mkdir release
if exist "release\PaperDownloaderUI" rmdir /s /q "release\PaperDownloaderUI"
mkdir "release\PaperDownloaderUI"

copy "dist\PaperDownloaderUI.exe" "release\PaperDownloaderUI\PaperDownloaderUI.exe" >nul
copy "README.md" "release\PaperDownloaderUI\README.md" >nul
copy ".env.example" "release\PaperDownloaderUI\.env.example" >nul

echo [DONE] Build output:
echo   %cd%\release\PaperDownloaderUI\PaperDownloaderUI.exe
pause

endlocal
