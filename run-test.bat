@echo off
setlocal
title AutoClaw Test Login

echo.
echo  ===================================================
echo     AutoClaw Auto-Login - Test Single Account
echo  ===================================================
echo.

set /p EMAIL="Email: "
set /p PASS="Password: "

echo.
echo  Testing: %EMAIL%
echo.

python "%~dp0autoclaw_autologin.py" "%EMAIL%:%PASS%"

echo.
echo  Done! Token saved to tokens.json if successful.
echo  Dashboard: http://localhost:31000
echo.
cmd /k
