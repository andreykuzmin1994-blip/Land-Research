@echo off
title Land Research - Stop
wsl -d Ubuntu --cd ~ -e bash Land-Research/home_machine/autopilot.sh stop
echo.
echo The loop stops after the experiment it is running now (up to 90 minutes).
pause
