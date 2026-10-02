@echo off
setlocal
cd /d "%~dp0"
python -c "import sys; raise SystemExit(sys.version_info < (3,10))" >nul 2>&1
if not errorlevel 1 (
    python stop.py
    exit /b
)
py -3 stop.py
if errorlevel 1 pause
