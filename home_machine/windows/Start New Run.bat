@echo off
rem Starts a fresh run (new branch, new baseline). Use after changing
rem parameters.json or prepare.py, or when the previous run ended.
call "%~dp0Start Land Research.bat" --new-run
