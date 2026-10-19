@echo off
rem Bot Hub launcher for Windows: double-click to start, close this window to stop.
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 -I hub.py) else (python -I hub.py)
pause
