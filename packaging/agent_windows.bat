@echo off
rem Runs the iyiCAM agent of this computer in the background and shows whether it works (docs\web.md): the agent only
rem shares folders of THIS computer with the web app, through the server (iyicam serve); it does no CAM work.
rem Usage:  packaging\agent_windows.bat -ServerUrl https://iyicam.example.com -ServerKey KEY -Root D:\Jobs   first time: keeps them, starts it
rem         packaging\agent_windows.bat             starts it if it is not running; shows its state
rem         packaging\agent_windows.bat -Stop       stops it
rem         packaging\agent_windows.bat -Restart    stops it and starts it again
rem Needs the 64-bit Python 3.11 or newer from python.org; makes .venv in this project (like web_windows.bat).
setlocal
title iyiCAM agent
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent_windows.ps1" %*
set "RC=%errorlevel%"
rem (a double-click opens a window that would close at once: wait for a key then)
echo %cmdcmdline% | find /i "/c" >nul && pause
exit /b %RC%
