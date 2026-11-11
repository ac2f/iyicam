@echo off
rem Starts, stops or shows the CNC-Assistant agent of this computer (a program of its own, installed apart from
rem iyiCAM; see agent_windows.ps1). Needs nothing of iyiCAM.
rem Usage:  packaging\agent_windows.bat             starts it if it is not running; shows its state and the client key
rem         packaging\agent_windows.bat -Stop       stops it
rem         packaging\agent_windows.bat -Restart    stops it and starts it again
setlocal
title CNC-Assistant agent
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent_windows.ps1" %*
set "RC=%errorlevel%"
rem (a double-click opens a window that would close at once: wait for a key then)
echo %cmdcmdline% | find /i "/c" >nul && pause
exit /b %RC%
