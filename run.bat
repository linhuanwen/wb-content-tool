@echo off
chcp 65001 >nul
title WB Content Tool

echo ========================================
echo   WB Content Tool - 启动中...
echo ========================================
echo.

cd /d "%~dp0"

:: 检查 .env 是否存在
if not exist ".env" (
    echo [提示] 未检测到 .env 配置文件
    echo 请复制 .env.template 为 .env 并填入你的 API Key
    echo.
)

:: 启动 Streamlit
streamlit run app.py --server.port 8501

pause
