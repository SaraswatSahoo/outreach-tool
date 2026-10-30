@echo off
rem Double-click to start Outreach Tool on Windows.
setlocal
cd /d "%~dp0"
title Outreach Tool

rem "where python" isn't enough: Windows ships a Microsoft Store alias named
rem python.exe even when Python isn't installed, so check that it really runs.
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "PY=python"

if not defined PY (
  echo.
  echo  Outreach Tool needs Python 3.11 or newer.
  echo  Opening the download page - install it, tick "Add python.exe to PATH",
  echo  then double-click this file again.
  echo.
  start "" https://www.python.org/downloads/
  pause
  exit /b 1
)

%PY% run.py %*
if errorlevel 1 (
  echo.
  echo  Outreach Tool stopped with an error - see the messages above.
  pause
)
