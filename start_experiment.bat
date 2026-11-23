@echo off
setlocal
cd /d "%~dp0"
if defined MATB_PYTHON goto use_matb_python
if exist "%~dp0runtime\python\python.exe" (
  set "MATB_PYTHON=%~dp0runtime\python\python.exe"
  goto use_matb_python
)
where py >nul 2>nul
if %errorlevel%==0 goto use_py
where python >nul 2>nul
if %errorlevel%==0 goto use_python
echo Python 3 was not found. Please install Python 3.
pause
exit /b 1
:use_py
py -3 launch_experiment.py
goto done
:use_matb_python
"%MATB_PYTHON%" launch_experiment.py
goto done
:use_python
python launch_experiment.py
:done
set "MATB_EXIT=%errorlevel%"
if not "%MATB_EXIT%"=="0" pause
endlocal & exit /b %MATB_EXIT%
