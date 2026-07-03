@echo off
cd /d "%~dp0"
title AutoClaw Proxy Server (Auto-Start)

REM ── Kill any existing proxy.py instances on ports 31000/18432 ──
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":31000 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":18432 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo Starting AutoClaw Proxy on http://localhost:31000
pythonw proxy.py
