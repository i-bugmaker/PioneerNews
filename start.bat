@echo off
chcp 65001 >nul
title PioneerNews Service
setlocal enabledelayedexpansion

if "%PORT%"=="" set PORT=10842

echo ========================================
echo    PioneerNews Finance News Service
echo ========================================
echo.

:: Clean up any process using the target port before starting
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "!PORT!" ^| findstr "LISTENING"') do (
    echo [INFO] Cleaning up port !PORT!...
    taskkill /F /PID %%a >nul 2>&1
)

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.8+.
    pause
    exit /b 1
)

:: Create virtual environment if not exists
if not exist "venv" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: Install dependencies (in venv)
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment appears corrupted.
    pause
    exit /b 1
)

echo [INFO] Checking dependencies...
venv\Scripts\python -m pip install --upgrade pip -q
venv\Scripts\pip install -r requirements.txt -q
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

:: Start service with venv
echo [INFO] Starting service on port !PORT!...
echo [INFO] URL: http://localhost:!PORT!
echo [TIP] Close this window to stop the service.
echo.

venv\Scripts\python main.py

if errorlevel 1 (
    echo.
    echo [ERROR] Service exited abnormally. Press any key to clean up port...
    pause
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr "!PORT!" ^| findstr "LISTENING"') do (
        taskkill /F /PID %%a >nul 2>&1
    )
    echo [INFO] Port released.
)

endlocal
pause
