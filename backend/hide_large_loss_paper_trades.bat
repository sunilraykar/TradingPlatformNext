@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Backend virtual environment not found at backend\.venv
    echo Run this file from the TradingPlatformNext\backend directory.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "scripts\hide_large_loss_paper_trades.py"
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" echo Script failed with exit code %RESULT%.
pause
exit /b %RESULT%
