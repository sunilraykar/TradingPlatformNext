@echo off
setlocal
title TradingPlatformNext - Start Backend and Frontend

REM Place this file in the TradingPlatformNext repository root.
set "PROJECT_DIR=%~dp0"
set "BACKEND_DIR=%PROJECT_DIR%backend"
set "FRONTEND_DIR=%PROJECT_DIR%frontend"

if not exist "%BACKEND_DIR%\app\main.py" (
    echo ERROR: Backend entry point not found:
    echo "%BACKEND_DIR%\app\main.py"
    echo Ensure this file is in the repository root.
    pause
    exit /b 1
)

if not exist "%FRONTEND_DIR%\package.json" (
    echo ERROR: Frontend package.json not found:
    echo "%FRONTEND_DIR%\package.json"
    echo Ensure this file is in the repository root.
    pause
    exit /b 1
)

if not exist "%FRONTEND_DIR%\node_modules" (
    echo ERROR: Frontend dependencies are missing.
    echo Run: cd /d "%FRONTEND_DIR%" ^&^& npm install
    pause
    exit /b 1
)

echo Starting TradingPlatformNext...
echo Backend API: http://127.0.0.1:8000
echo Frontend UI: http://127.0.0.1:5173
echo.

REM Launch the backend using the virtual-environment interpreter when available.
if exist "%BACKEND_DIR%\.venv\Scripts\python.exe" (
    start "TradingPlatformNext Backend" cmd /k "cd /d ""%BACKEND_DIR%"" && ""%BACKEND_DIR%\.venv\Scripts\python.exe"" -m uvicorn app.main:app --reload"
) else (
    where py >nul 2>&1
    if not errorlevel 1 (
        start "TradingPlatformNext Backend" cmd /k "cd /d ""%BACKEND_DIR%"" && py -3.11 -m uvicorn app.main:app --reload"
    ) else (
        start "TradingPlatformNext Backend" cmd /k "cd /d ""%BACKEND_DIR%"" && python -m uvicorn app.main:app --reload"
    )
)

REM Launch the frontend in a separate command window.
start "TradingPlatformNext Frontend" cmd /k "cd /d ""%FRONTEND_DIR%"" && npm run dev"

echo Startup commands launched in separate windows.
echo Check each window for startup errors.
endlocal
