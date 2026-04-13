@echo off
chcp 65001 >nul
title PioneerNews 服务启动器
echo ========================================
echo    PioneerNews 财经新闻服务启动器
echo ========================================
echo.

:: 检查虚拟环境
if not exist "venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境，请先创建虚拟环境
    echo 运行: python -m venv venv
    pause
    exit /b 1
)

:: 激活虚拟环境并启动服务
echo [信息] 正在启动服务...
echo [信息] 访问地址: http://localhost:10842
echo.
call venv\Scripts\activate.bat && python main.py

pause
