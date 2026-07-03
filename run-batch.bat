@echo off
setlocal
title AutoClaw Batch Login

echo.
echo  ===================================================
echo     AutoClaw Auto-Login - Batch Mode
echo  ===================================================
echo.

echo  Make sure proxy.py is running first!
echo  Start it with: start-proxy.bat
echo.

python "%~dp0autoclaw_autologin.py" --batch "%~dp0accounts.txt" --interactive %*

echo.
echo  ===================================================
echo  Script finished. Window will stay open.
echo  Dashboard: http://localhost:31000
echo  ===================================================
echo.
cmd /k
