@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 exit /b 1
call :run
set "VIDEOGET_EXIT=%ERRORLEVEL%"
if not "%VIDEOGET_EXIT%"=="0" echo Startup failed. Exit code: %VIDEOGET_EXIT%
if not "%VIDEOGET_EXIT%"=="0" if /I not "%~1"=="--no-pause" pause
popd
endlocal & exit /b %VIDEOGET_EXIT%

:run
set "VIDEOGET_PYTHON=py -3"
where py >nul 2>nul
if errorlevel 1 set "VIDEOGET_PYTHON=python"
%VIDEOGET_PYTHON% -c "import sys; assert sys.platform == 'win32', 'Run this batch file on Windows'; assert sys.version_info >= (3, 11), 'Python 3.11 or newer is required'"
if errorlevel 1 exit /b %ERRORLEVEL%
if exist ".venv\Scripts\python.exe" goto :run_venv_ready
%VIDEOGET_PYTHON% -m venv .venv
if errorlevel 1 exit /b %ERRORLEVEL%

:run_venv_ready
".venv\Scripts\python.exe" -c "import sys; assert sys.platform == 'win32' and sys.version_info >= (3, 11), 'The existing .venv must use Windows Python 3.11 or newer'"
if errorlevel 1 exit /b %ERRORLEVEL%
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b %ERRORLEVEL%
".venv\Scripts\python.exe" main.py
exit /b %ERRORLEVEL%
