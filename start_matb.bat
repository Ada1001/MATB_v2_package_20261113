@echo off
setlocal
cd /d "%~dp0"
if defined MATB_PYTHON goto launch
if exist "%~dp0runtime\python\python.exe" (
  set "MATB_PYTHON=%~dp0runtime\python\python.exe"
  goto launch
)
set "MATB_PYTHON=python"
:launch
"%MATB_PYTHON%" online_agmnet\launch_demo.py
if errorlevel 1 (
  echo Set MATB_PYTHON to a Python environment with torch, geoopt, mne, scipy and numpy.
  pause
  exit /b 1
)
endlocal
