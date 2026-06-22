@echo off
chcp 65001 >nul
title WB Content Tool — 打包

echo ========================================
echo   WB Content Tool — 打包分发
echo ========================================
echo.

set "DIST_DIR=%~dp0dist\WB-Content-Tool"

:: 清理旧分发目录
if exist "%DIST_DIR%" (
    echo [1/4] 清理旧分发目录...
    rd /s /q "%DIST_DIR%"
) else (
    echo [1/4] 创建分发目录...
)
mkdir "%DIST_DIR%"

echo [2/4] 复制项目文件...

:: 核心 Python 模块
copy "%~dp0app.py"           "%DIST_DIR%\" >nul
copy "%~dp0config.py"        "%DIST_DIR%\" >nul
copy "%~dp0crawler.py"       "%DIST_DIR%\" >nul
copy "%~dp0crawler_ui.py"    "%DIST_DIR%\" >nul
copy "%~dp0excel_io.py"      "%DIST_DIR%\" >nul
copy "%~dp0extractor.py"     "%DIST_DIR%\" >nul
copy "%~dp0translator.py"    "%DIST_DIR%\" >nul
copy "%~dp0translator_ui.py" "%DIST_DIR%\" >nul

:: 配置文件
copy "%~dp0requirements.txt"  "%DIST_DIR%\" >nul
copy "%~dp0.env.template"     "%DIST_DIR%\" >nul

:: Prompt
mkdir "%DIST_DIR%\prompts" 2>nul
copy "%~dp0prompts\translation_persona.txt" "%DIST_DIR%\prompts\" >nul

:: Streamlit 配置
mkdir "%DIST_DIR%\.streamlit" 2>nul
copy "%~dp0.streamlit\config.toml" "%DIST_DIR%\.streamlit\" >nul

:: 文档
copy "%~dp0README.md"   "%DIST_DIR%\" >nul
copy "%~dp0CONTEXT.md"  "%DIST_DIR%\" >nul

:: 示例 ASIN 文件
copy "%~dp0案例 asin（采集用）.xlsx" "%DIST_DIR%\" >nul

echo [3/4] 生成启动脚本...

:: 启动脚本（目标电脑用）
(
echo @echo off
echo chcp 65001 ^>nul
echo title WB Content Tool
echo.
echo cd /d "%%~dp0"
echo.
echo :: 检查虚拟环境
echo if not exist "venv\Scripts\python.exe" ^(
echo     echo [错误] 未找到虚拟环境，请先运行 安装.bat
echo     pause
echo     exit /b 1
echo ^)
echo.
echo :: 激活虚拟环境并启动
echo call venv\Scripts\activate.bat
echo streamlit run app.py --server.port 8501
echo pause
) > "%DIST_DIR%\启动.bat"

:: 安装脚本
(
echo @echo off
echo chcp 65001 ^>nul
echo title WB Content Tool — 安装
echo.
echo ========================================
echo   WB Content Tool — 一键安装
echo ========================================
echo.
echo.
echo [检查] 正在检测 Python...
echo.
echo where python ^>nul 2^>^&1
echo if %%errorlevel%% neq 0 ^(
echo     echo [错误] 未检测到 Python！
echo     echo.
echo     echo 请先安装 Python 3.12+：
echo     echo   https://www.python.org/downloads/
echo     echo.
echo     echo ** 安装时务必勾选 "Add Python to PATH" **
echo     pause
echo     exit /b 1
echo ^)
echo.
echo [1/3] 创建虚拟环境...
echo python -m venv venv
echo.
echo [2/3] 安装 Python 依赖...
echo call venv\Scripts\activate.bat
echo pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
echo [3/3] 初始化配置文件...
echo if not exist ".env" ^(
echo     copy .env.template .env ^>nul
echo     echo [提示] 已从模板创建 .env 文件，请编辑填入你的 API Key
echo ^)
echo.
echo echo.
echo echo ========================================
echo echo   安装完成！
echo echo ========================================
echo echo.
echo echo 下一步：
echo echo   1. 编辑 .env 文件，填入你的 API Key
echo echo   2. 双击 启动.bat 即可运行
echo echo.
echo pause
) > "%DIST_DIR%\安装.bat"

echo [4/4] 打包为 ZIP...
set "ZIP_FILE=%~dp0dist\WB-Content-Tool.zip"
if exist "%ZIP_FILE%" del "%ZIP_FILE%"

:: 使用 PowerShell 压缩（Windows 10+ 自带）
powershell -NoProfile -Command ^
    "Compress-Archive -Path '%DIST_DIR%' -DestinationPath '%ZIP_FILE%' -Force"

echo.
echo ========================================
echo   打包完成！
echo ========================================
echo.
echo   位置: dist\WB-Content-Tool.zip
echo   解压后双击 安装.bat → 配置 .env → 双击 启动.bat
echo.
pause
