
@echo off
setlocal

title TradingPlatformNext - Main Backup

echo ==========================================
echo   TradingPlatformNext - Main Backup
echo ==========================================
echo.

:: Move to the directory containing this batch file
cd /d "%~dp0"

:: Fetch latest main from GitHub
echo Fetching latest main from GitHub...
git fetch origin main

if errorlevel 1 (
    echo.
    echo ERROR: Git fetch failed. Backup cancelled.
    pause
    exit /b 1
)

:: Create timestamp
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TIMESTAMP=%%i"

:: Create backup directory
set "BACKUP_DIR=%CD%\backup\main_%TIMESTAMP%"
mkdir "%BACKUP_DIR%"

if errorlevel 1 (
    echo ERROR: Could not create backup directory.
    pause
    exit /b 1
)

:: Create source ZIP from latest origin/main
echo.
echo Creating source ZIP...
git archive --format=zip --output="%BACKUP_DIR%\main_source.zip" origin/main

if errorlevel 1 (
    echo ERROR: Source ZIP creation failed.
    pause
    exit /b 1
)

:: Create Git bundle with repository history and references
echo Creating Git history bundle...
git bundle create "%BACKUP_DIR%\main_history.bundle" --all

if errorlevel 1 (
    echo ERROR: Git bundle creation failed.
    pause
    exit /b 1
)

:: Save commit SHA
git rev-parse origin/main > "%BACKUP_DIR%\commit.txt"

echo.
echo ==========================================
echo   BACKUP COMPLETED SUCCESSFULLY
echo ==========================================
echo.
echo Backup location:
echo %BACKUP_DIR%
echo.
echo Files created:
echo   main_source.zip
echo   main_history.bundle
echo   commit.txt
echo.

pause
endlocal
