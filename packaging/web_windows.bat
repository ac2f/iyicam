@echo off
rem Starts the iyiCAM web app (docs\web.md): own environment in .venv, own key, no Qt needed. The CAM work runs in the
rem browser; with --local the folders of THIS computer (--root D:\Jobs, repeatable) are shared with it, otherwise an agent shares them.
rem Needs the 64-bit Python 3.11 or newer from python.org.
rem Usage:  packaging\web_windows.bat [--local --root D:\Jobs] [--port N] [--host 127.0.0.1]     (default: every network, port 8300)
rem         packaging\web_windows.bat --show-key | --new-key
setlocal
cd /d "%~dp0\.."
title iyiCAM web

set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% -c "import struct, sys; sys.exit(0 if struct.calcsize('P') == 8 and sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo.
  echo This needs the 64-bit Python 3.11 or newer ^(python.org, "Windows installer ^(64-bit^)"^).
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Making the environment ^(.venv^) ...
  %PY% -m venv .venv || (pause & exit /b 1)
)
".venv\Scripts\python.exe" -c "import iyicam.share.server" >nul 2>nul
if errorlevel 1 (
  echo Installing iyiCAM ...
  ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -e . || (pause & exit /b 1)
)

set "ARGS=%*"
if "%ARGS%"=="" set "ARGS=--host 0.0.0.0"
echo %ARGS% | findstr /i /c:"--host" /c:"--show-key" /c:"--new-key" >nul || set "ARGS=--host 0.0.0.0 %ARGS%"
".venv\Scripts\python.exe" -m iyicam serve %ARGS%
if errorlevel 1 pause
