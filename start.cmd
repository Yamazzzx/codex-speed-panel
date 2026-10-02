@echo off
setlocal
cd /d "%~dp0"
python -c "import sys; raise SystemExit(sys.version_info < (3,10))" >nul 2>&1
if not errorlevel 1 (
    python launch.pyw %*
    if errorlevel 1 pause
    exit /b
)
py -3 launch.pyw %*
if errorlevel 1 (
    echo Python 3.10 or newer is required. Install Python and try again.
    pause
)
