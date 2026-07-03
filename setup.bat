@echo off
setlocal
title AutoClaw Auto-Login - Setup

echo.
echo  ===================================================
echo     AutoClaw Auto-Login - First Time Setup
echo  ===================================================
echo.

REM ── Check Python installed ──
echo  Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] Python is NOT installed!
    echo.
    echo  Please install Python 3.10+ from:
    echo     https://www.python.org/downloads/
    echo.
    echo  IMPORTANT: Check "Add Python to PATH" during install.
    echo.
    echo  After installing Python, run this setup again.
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  Python found: %PYVER%

REM ── Check pip ──
pip --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] pip not found. Reinstall Python with pip enabled.
    pause
    exit /b 1
)

REM ── Create accounts.txt from template if not exists ──
if not exist "accounts.txt" (
    if exist "accounts.txt.example" (
        copy "accounts.txt.example" "accounts.txt" >nul
        echo  Created accounts.txt from template.
        echo  ^>^> Edit accounts.txt and add your email:password lines ^<^<
    )
)

echo.
echo  Installing Python dependencies...
pip install flask requests cloakbrowser aiohttp 2>nul
if errorlevel 1 (
    echo  [ERROR] pip install failed. Make sure Python is installed.
    pause
    exit /b 1
)

echo.
echo  Pre-downloading CloakBrowser stealth Chromium binary (~535MB)...
python -m cloakbrowser install
if errorlevel 1 (
    echo  [WARNING] CloakBrowser binary download failed.
    echo  It will auto-download on first run instead.
)

echo.
echo  Installing Playwright system dependencies...
python -m playwright install-deps chromium 2>nul

echo.
echo  ===================================================
echo  Setup complete!
echo.
echo  Next steps:
echo    1. Edit accounts.txt - add email:password per line
echo    2. Double-click start-proxy.bat to start proxy
echo    3. Double-click run-batch.bat to login all accounts
echo    4. Or double-click run-test.bat to test single account
echo.
echo  CloakBrowser binary: ~/.cloakbrowser\
echo  Dashboard: http://localhost:31000
echo  ===================================================
echo.
pause
