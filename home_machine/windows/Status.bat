@echo off
title Land Research - Status
wsl -d Ubuntu --cd ~ -e bash Land-Research/home_machine/autopilot.sh status
echo.
pause
