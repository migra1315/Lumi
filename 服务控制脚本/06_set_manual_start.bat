@echo off
setlocal
set SERVICE_NAME=RobotControlSystem

net session >nul 2>&1
if not "%errorlevel%"=="0" (
    echo Please run this script as Administrator.
    pause
    exit /b 1
)

echo Setting service startup type to manual: %SERVICE_NAME%
sc.exe config "%SERVICE_NAME%" start= demand
echo.
sc.exe qc "%SERVICE_NAME%"
echo.
pause
