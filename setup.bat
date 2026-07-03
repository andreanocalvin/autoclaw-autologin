@echo off
setlocal
title AutoClaw Proxy Setup (CloakBrowser)

echo.
echo  ===================================================
echo     AutoClaw Proxy - First Time Setup (CloakBrowser)
echo  ===================================================
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
