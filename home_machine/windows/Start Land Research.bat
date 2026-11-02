@echo off
setlocal
cd /d "%~dp0"
title Land Research

wsl -d Ubuntu -e true >nul 2>&1
if errorlevel 1 (
  echo Ubuntu on WSL is not installed. Run Install.bat first.
  pause
  exit /b 1
)

for /f "usebackq delims=" %%P in (`wsl -d Ubuntu -e wslpath -a "%~dp0settings.txt"`) do set "SETTINGS=%%P"
wsl -d Ubuntu --cd ~ -e bash Land-Research/home_machine/autopilot.sh start "%SETTINGS%" %*
if errorlevel 1 (
  echo.
  echo The loop did not start. Read the messages above.
  pause
  exit /b 1
)

echo.
echo The loop is running. This window shows it live.
echo Minimize it, but do not close it: it keeps Ubuntu running in the background.
echo To stop: double-click "Stop Land Research.bat".
echo.
wsl -d Ubuntu -e bash -c "tail -n 20 -f ~/.land-research/autopilot.log"
