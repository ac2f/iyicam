@echo off
rem Opens the iyiCAM desktop program (no console window). Double-click it whenever you want the program.
rem It is a program of its own: it does not touch the CNC-Assistant agent, and the agent needs it neither open nor closed.
rem The first time it makes the environment (.venv-build, with the Qt interface) and installs iyiCAM into it.
rem Needs the 64-bit Python 3.11 or newer from python.org (Qt for Python has no 32-bit version).
rem Usage:  packaging\desktop_windows.bat [project.icam]
setlocal
cd /d "%~dp0\.."

if not exist ".venv-build\Scripts\pythonw.exe" (
  set "PY=python"
  where py >nul 2>nul && set "PY=py -3"
  %PY% -c "import struct, sys; sys.exit(0 if struct.calcsize('P') == 8 and sys.version_info >= (3, 11) else 1)"
  if errorlevel 1 (
    echo.
    echo This needs the 64-bit Python 3.11 or newer ^(python.org, "Windows installer ^(64-bit^)"^).
    pause
    exit /b 1
  )
  echo Making the environment ^(.venv-build^) ...
  %PY% -m venv .venv-build || (pause & exit /b 1)
)
".venv-build\Scripts\python.exe" -c "import PySide6, iyicam" >nul 2>nul
if errorlevel 1 (
  echo Installing iyiCAM with its window ^(a few minutes the first time^) ...
  ".venv-build\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -e ".[gui]" || (pause & exit /b 1)
)
start "" ".venv-build\Scripts\pythonw.exe" -m iyicam %*
