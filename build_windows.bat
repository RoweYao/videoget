@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 exit /b 1
call :build
set "VIDEOGET_EXIT=%ERRORLEVEL%"
if not "%VIDEOGET_EXIT%"=="0" echo Build failed. Exit code: %VIDEOGET_EXIT%
if /I not "%~1"=="--no-pause" pause
popd
endlocal & exit /b %VIDEOGET_EXIT%

:build
set "VIDEOGET_PYTHON=py -3"
where py >nul 2>nul
if errorlevel 1 set "VIDEOGET_PYTHON=python"
%VIDEOGET_PYTHON% -c "import platform, struct, sys; assert sys.platform == 'win32', 'Build on Windows'; assert sys.version_info >= (3, 11), 'Python 3.11 or newer is required'; assert struct.calcsize('P') == 8 and platform.machine().lower() in ('amd64', 'x86_64'), '64-bit x86 Windows Python is required'"
if errorlevel 1 exit /b %ERRORLEVEL%
if exist ".venv\Scripts\python.exe" goto :build_venv_ready
%VIDEOGET_PYTHON% -m venv .venv
if errorlevel 1 exit /b %ERRORLEVEL%

:build_venv_ready
".venv\Scripts\python.exe" -c "import platform, struct, sys; assert sys.platform == 'win32' and sys.version_info >= (3, 11) and struct.calcsize('P') == 8 and platform.machine().lower() in ('amd64', 'x86_64'), 'The existing .venv must use 64-bit x86 Windows Python 3.11 or newer'"
if errorlevel 1 exit /b %ERRORLEVEL%
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b %ERRORLEVEL%
".venv\Scripts\python.exe" -m pip install --upgrade -r requirements-build.txt
if errorlevel 1 exit /b %ERRORLEVEL%
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm VideoGet.spec
if errorlevel 1 exit /b %ERRORLEVEL%
if not exist "dist\VideoGet.exe" exit /b 1
echo Build complete: "%CD%\dist\VideoGet.exe"
exit /b 0
