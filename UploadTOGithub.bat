
@echo off
setlocal

title TradingPlatformNext - Full GitHub Backup

cd /d "%~dp0"

echo ================================================
echo   TradingPlatformNext - Full GitHub Backup
echo ================================================
echo.

:: Verify Git repository
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo ERROR: This folder is not a Git repository.
    pause
    exit /b 1
)

:: Ensure main branch
echo Switching to main branch...
git switch main

if errorlevel 1 (
    echo ERROR: Could not switch to main.
    echo Check for uncommitted changes or resolve conflicts.
    pause
    exit /b 1
)

:: Get latest GitHub changes
echo.
echo Pulling latest changes from GitHub...
git pull --rebase origin main

if errorlevel 1 (
    echo ERROR: Pull failed. Resolve the Git issue before continuing.
    pause
    exit /b 1
)

:: Stage everything in root, including ignored backup folder
echo.
echo Staging all repository files...
git add -A

:: Explicitly include backup folder, even if ignored by .gitignore
if exist "backup" (
    git add -f backup
)

:: Display staged changes
echo.
echo ================================================
echo   FILES TO BE BACKED UP
echo ================================================
git status --short

echo.
echo IMPORTANT:
echo Review the staged files above.
echo Ensure no passwords, API keys, access tokens,
echo .env files, or other secrets are being committed.
echo.

choice /C YN /M "Continue with GitHub backup"

if errorlevel 2 (
    echo.
    echo Backup cancelled. No commit or push performed.
    pause
    exit /b 0
)

:: Commit changes only when necessary
git diff --cached --quiet

if errorlevel 1 (
    echo.
    echo Creating backup commit...
    git commit -m "Full project backup including local backup folder"

    if errorlevel 1 (
        echo ERROR: Commit failed.
        pause
        exit /b 1
    )
) else (
    echo.
    echo No new changes to commit.
)

:: Push to GitHub
echo.
echo Pushing main to GitHub...
git push origin main

if errorlevel 1 (
    echo.
    echo ERROR: GitHub push failed.
    pause
    exit /b 1
)

echo.
echo ================================================
echo   GITHUB BACKUP COMPLETED
echo ================================================
echo.
echo Repository:
echo https://github.com/sunilraykar/TradingPlatformNext
echo.
echo Branch: main
echo Included: Root repository files and backup folder
echo.

git log -1 --oneline

pause
endlocal
