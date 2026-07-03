@echo off
REM AutoClaw Auto-Login - Quick launcher
REM Usage: autoclaw-login.bat [options] [email:password ...]
REM Run autoclaw-login.bat --help for full options

python "%~dp0autoclaw_autologin.py" %*
if errorlevel 1 pause
