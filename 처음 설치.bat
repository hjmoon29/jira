@echo off
rem Work Bot first-time setup for Windows (run once per PC)
chcp 65001 >nul
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY (where python >nul 2>nul && set PY=python)
if not defined PY (
  echo [!] Python not found. Install Python 3.8+ from https://www.python.org  - check "Add python.exe to PATH"
  pause
  exit /b 1
)
echo [1/2] Installing packages: playwright, openpyxl
%PY% -m pip install --user --quiet playwright openpyxl
echo [2/2] Jira token and Claude Code skills
%PY% setup.py
echo.
echo Done. Start the bot with the run .bat file in this folder, then open http://127.0.0.1:8765/
pause
