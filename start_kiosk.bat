@echo off
setlocal

set "CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"

if not exist "%CHROME_EXE%" (
    echo Chrome executable not found.
    echo Checked:
    echo   C:\Program Files\Google\Chrome\Application\chrome.exe
    pause
    exit /b 1
)

timeout /t 10 /nobreak >nul

start "" "%CHROME_EXE%" ^
  --kiosk ^
  --no-first-run ^
  --disable-session-crashed-bubble ^
  --disable-infobars ^
  --overscroll-history-navigation=0 ^
  "http://192.168.110.5:3888/touch/homepage.html"

exit /b 0
