@echo off
chcp 65001 >nul
title PioneerNews Service
echo ========================================
echo    PioneerNews Finance News Service
echo ========================================
echo.

:: Clean up any process using port 10842 before starting
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "10842" ^| findstr "LISTENING"') do (
    echo [INFO] Cleaning up port 10842...
    taskkill /F /PID %%a >nul 2>&1
)

:: Check virtual environment
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found.
    echo Run: python -m venv venv
    pause
    exit /b 1
)

:: Activate virtual environment and start service
echo [INFO] Starting service...
echo [INFO] URL: http://localhost:10842
echo [TIP] Close this window to stop the service.
echo.

call venv\Scripts\activate.bat
python main.py

if errorlevel 1 (
    echo.
    echo [ERROR] Service exited abnormally. Press any key to clean up port...
    pause
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr "10842" ^| findstr "LISTENING"') do (
        taskkill /F /PID %%a >nul 2>&1
    )
    echo [INFO] Port released.
)

pause
