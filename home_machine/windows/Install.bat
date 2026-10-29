@echo off
setlocal
cd /d "%~dp0"
title Land Research - Install

findstr /r /c:"^ *DATABASE_URL *= *postgres" settings.txt >nul
if errorlevel 1 (
  echo Fill in DATABASE_URL in settings.txt, save it, then run Install.bat again.
  start notepad settings.txt
  pause
  exit /b 1
)

wsl -d Ubuntu -e true >nul 2>&1
if errorlevel 1 (
  echo Installing Ubuntu on WSL. Windows will ask for administrator permission.
  wsl --install -d Ubuntu
  echo.
  echo Restart the computer. After the restart, Ubuntu opens and asks you to
  echo choose a username and password. Then run Install.bat again.
  pause
  exit /b 0
)

for /f "usebackq delims=" %%P in (`wsl -d Ubuntu -e wslpath -a "%~dp0settings.txt"`) do set "SETTINGS=%%P"
for /f "usebackq delims=" %%P in (`wsl -d Ubuntu -e wslpath -a "%~dp0bootstrap.sh"`) do set "BOOT=%%P"
wsl -d Ubuntu --cd ~ -e bash "%BOOT%" "%SETTINGS%"
if errorlevel 1 (
  echo.
  echo Setup did not finish. Read the messages above, fix the problem, and run Install.bat again.
  pause
  exit /b 1
)

echo.
choice /m "Start Land Research automatically when you sign in to Windows"
if errorlevel 2 goto done
set "LR_DIR=%~dp0"
powershell -NoProfile -Command "$d=$env:LR_DIR; $s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Land Research.lnk'); $s.TargetPath=$d+'Start Land Research.bat'; $s.Arguments='--autostart'; $s.WorkingDirectory=$d; $s.WindowStyle=7; $s.Save()"
echo Added to Windows startup. It resumes the run after you sign in, unless you pressed Stop.

:done
echo.
echo Setup finished. Double-click "Start Land Research.bat" to begin.
echo Also set Windows to never sleep: Settings ^> System ^> Power ^> Sleep: Never.
pause
