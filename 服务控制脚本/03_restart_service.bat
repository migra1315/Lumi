@echo off
setlocal
set SERVICE_NAME=RobotControlSystem

net session >nul 2>&1
if not "%errorlevel%"=="0" (
    echo Please run this script as Administrator.
    pause
    exit /b 1
)

echo Stopping service: %SERVICE_NAME%
sc.exe stop "%SERVICE_NAME%"
echo Waiting for service to stop...
timeout /t 5 /nobreak >nul
echo.
echo Starting service: %SERVICE_NAME%
sc.exe start "%SERVICE_NAME%"
echo.
sc.exe query "%SERVICE_NAME%"
echo.
pause
