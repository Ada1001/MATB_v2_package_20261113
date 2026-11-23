@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 goto use_py
python -m pip install -r requirements_eeg.txt
goto done
:use_py
py -m pip install -r requirements_eeg.txt
:done
pause
endlocal
