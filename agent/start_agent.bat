@echo off
echo Starting Ghost Monitor...

set PYTHON=pythonw.exe
set AGENT=%~dp0monitor_agent.py
set TRAY=%~dp0tray_icon.py

start "" "%PYTHON%" "%AGENT%"
start "" "%PYTHON%" "%TRAY%"

echo Ghost Monitor agent and tray indicator launched.
echo Check your system tray for the Ghost Monitor icon.