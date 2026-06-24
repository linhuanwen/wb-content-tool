@echo off
chcp 65001 >nul
title WB Content Tool

echo ========================================
echo   WB Content Tool - 启动中...
echo ========================================
echo.

cd /d "%~dp0"

:: 检查 .env 是否存在（首次运行会自动创建）
if not exist ".env" (
    echo [提示] .env 配置文件将在首次启动时自动创建
    echo.
)

:: 启动 Streamlit
streamlit run app.py --server.port 8501

pause
