@echo off
setlocal
set SERVICE_NAME=RobotControlSystem

echo Querying service status: %SERVICE_NAME%
echo.
sc.exe query "%SERVICE_NAME%"
echo.
sc.exe qc "%SERVICE_NAME%"
echo.
pause
