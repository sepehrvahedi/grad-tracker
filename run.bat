@echo off
rem One command for Windows:  run.bat        (try it first with:  run.bat demo)
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv\Scripts\python.exe (
  echo First run: setting up, about a minute...
  %PY% -m venv .venv || (echo Grad Tracker needs Python 3.9 or newer: https://www.python.org/downloads/ & exit /b 1)
  .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
  .venv\Scripts\python.exe -m pip install --quiet -e .
)
.venv\Scripts\python.exe -m gradtracker %*
