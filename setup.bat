@echo off
:: CryptoTrader — לחץ פעמיים להפעלה
:: עובד מ-cmd, מ-Explorer, ומכל מקום

:: הפעל PowerShell עם הסקריפט המלא
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/roiag/cryptoTrader/master/setup.ps1 | iex"

:: אם PowerShell נכשל — הצג שגיאה ברורה
if %ERRORLEVEL% neq 0 (
    echo.
    echo   [ERROR] Setup failed. See messages above.
    echo.
    pause
    exit /b 1
)
