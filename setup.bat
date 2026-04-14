@echo off
title CryptoTrader Setup
cls

echo.
echo   ============================================
echo     CryptoTrader -- Setup Starting...
echo   ============================================
echo.

:: בדוק שsetup.ps1 נמצא באותה תיקייה
if not exist "%~dp0setup.ps1" (
    echo   [ERROR] setup.ps1 not found next to setup.bat
    echo.
    echo   Make sure BOTH files are in the same folder:
    echo     - setup.bat
    echo     - setup.ps1
    echo.
    pause
    exit /b 1
)

:: הרץ את הסקריפט המלא
powershell -ExecutionPolicy Bypass -File "%~dp0setup.ps1"

if %ERRORLEVEL% neq 0 (
    echo.
    echo   [ERROR] Setup failed. See messages above.
    echo.
    pause
    exit /b 1
)

pause
