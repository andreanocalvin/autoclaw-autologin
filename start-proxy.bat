@echo off
setlocal
title AutoClaw Proxy Server

echo.
echo  ===================================================
echo     AutoClaw Proxy Server
echo  ===================================================
echo.

REM ── Kill any existing proxy.py instances on port 31000 ──
echo  Checking for existing proxy instances...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":31000 " ^| findstr "LISTENING"') do (
    echo  Killing old process PID %%a on port 31000...
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":18432 " ^| findstr "LISTENING"') do (
    echo  Killing old process PID %%a on port 18432...
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo.
echo  Starting proxy on port 31000...
echo  Dashboard: http://localhost:31000
echo  API:       http://localhost:31000/v1/chat/completions
echo  Callback:  http://localhost:18432/auth/callback-google
echo.
echo  Press Ctrl+C to stop.
echo.

python "%~dp0proxy.py"

echo.
echo  Proxy stopped.
pause
