@echo off
rem Builds iyiCAM for Windows: a folder (dist\iyiCAM), a zip and, with --installer, a setup program.
rem Needs the 64-bit Python 3.11 or newer from python.org (Qt for Python has no 32-bit version).
rem Usage:  packaging\build_windows.bat [--installer] [--no-test]
setlocal
cd /d "%~dp0\.."

set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% -c "import struct, sys; sys.exit(0 if struct.calcsize('P') == 8 and sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo.
  echo This needs the 64-bit Python 3.11 or newer ^(python.org, "Windows installer ^(64-bit^)"^).
  echo Your Python:
  %PY% -c "import struct, sys; print('  ', sys.version.split()[0], str(struct.calcsize('P') * 8) + '-bit')"
  exit /b 1
)

if not exist ".venv-build\Scripts\python.exe" (
  %PY% -m venv .venv-build || exit /b 1
)
call ".venv-build\Scripts\activate.bat"
python -m pip install --upgrade pip || exit /b 1
python -m pip install ".[gui]" pyinstaller || exit /b 1
python packaging\build.py %* || exit /b 1
echo.
echo Done: dist\iyiCAM\iyiCAM.exe
