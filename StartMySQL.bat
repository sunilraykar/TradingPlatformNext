@echo off
setlocal
title TradingPlatform MySQL SQL Terminal

set "MYSQL_EXE=C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe"
set "SERVICE_NAME=MySQL84"
set "DATABASE=trading_platform_v2"

if not exist "%MYSQL_EXE%" (
    echo ERROR: mysql.exe was not found:
    echo "%MYSQL_EXE%"
    echo.
    pause
    exit /b 1
)

echo Checking MySQL service "%SERVICE_NAME%"...
sc query "%SERVICE_NAME%" | findstr /I "RUNNING" >nul
if errorlevel 1 (
    echo MySQL is not running. Attempting to start it...
    net start "%SERVICE_NAME%"
    if errorlevel 1 (
        echo.
        echo Could not start the service. If access is denied, right-click
        echo this batch file and choose "Run as administrator".
        echo If the service name differs, check it with:
        echo powershell -Command "Get-Service *mysql*"
        echo.
        pause
        exit /b 1
    )
)

echo.
echo Opening MySQL terminal with database "%DATABASE%" selected...
echo Enter the MySQL root password when prompted.
echo Type "exit" to leave the SQL terminal.
echo.
"%MYSQL_EXE%" --user=root --password --database="%DATABASE%"
set "MYSQL_EXIT=%ERRORLEVEL%"

echo.
if not "%MYSQL_EXIT%"=="0" (
    echo MySQL client exited with code %MYSQL_EXIT%.
)
pause
exit /b %MYSQL_EXIT%
