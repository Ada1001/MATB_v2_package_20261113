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
echo Starting MATB demo with live Neuracle EEG and saved predictions...
"%MATB_PYTHON%" online_agmnet\demo_replay_server.py --port 8767 --live-eeg --open-browser
if errorlevel 1 (
  pause
  exit /b 1
)
endlocal
