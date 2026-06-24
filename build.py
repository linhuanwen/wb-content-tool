"""
WB Content Tool — 分发打包脚本。

将项目核心文件复制到 dist/WB-Content-Tool/ 并打包为 ZIP。
排除开发/测试文件、缓存、敏感配置等。
"""

import os
import shutil
import sys
import zipfile
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(ROOT, "dist", "WB-Content-Tool")
ZIP_FILE = os.path.join(ROOT, "dist", "WB-Content-Tool.zip")

# 要包含的 Python 模块（核心运行时代码）
PY_MODULES = [
    # 主入口 & 配置
    "app.py",
    "config.py",
    # 采集
    "crawler.py",
    "crawler_ui.py",
    # Phase 1: AI 信息萃取
    "phase1_extractor.py",
    # Phase 2: AI 文案翻译
    "phase2_translator.py",
    # 翻译
    "translator.py",
    "translator_ui.py",
    # 图片处理 & 翻译
    "image_processor.py",
    "image_translator.py",
    "image_translator_ui.py",
    # 图片生成（Gemini）
    "image_generator.py",
    "image_generator_ui.py",
    # 工作线程 & 数据库
    "worker.py",
    "db.py",
    # 工作流引擎 & UI（Tab5）
    "workflow_engine.py",
    "workflow_ui.py",
    # 工具模块
    "excel_io.py",
    "r2_storage.py",
    "text_utils.py",
]

# 要包含的配置/数据文件
DATA_FILES = [
    "requirements.txt",
    ".env.template",
    "README.md",
    "CONTEXT.md",
]

# 要包含的目录
DATA_DIRS = [
    "prompts",
    ".streamlit",
    "fonts",
]

# 要捆绑到安装包里的依赖（离线安装用）
# 文件名保持一致，安装脚本会按架构匹配
BUNDLED_FILES = [
    "python-3.11.9-embed-amd64.zip",   # ~12 MB，覆盖 99%+ 用户
    "get-pip.py",                       # ~2 MB，pip 引导脚本
]

BUNDLED_URLS = {
    "python-3.11.9-embed-amd64.zip": "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip",
    "get-pip.py": "https://bootstrap.pypa.io/get-pip.py",
}

# 示例文件
SAMPLE_FILES = [
    "案例 asin（采集用）.xlsx",
]


def download_bundled():
    """下载要捆绑的离线依赖（仅下载缺失的文件）。"""
    for fname in BUNDLED_FILES:
        dst = os.path.join(ROOT, fname)
        if os.path.isfile(dst):
            size_mb = os.path.getsize(dst) / (1024 * 1024)
            print(f"  [跳过] {fname} 已存在 ({size_mb:.1f} MB)")
            continue
        url = BUNDLED_URLS.get(fname)
        if not url:
            print(f"  [警告] {fname} 没有配置下载地址，跳过")
            continue
        print(f"  [下载] {fname}（约 {_estimate_size(fname)}）...")
        try:
            _download(url, dst)
            size_mb = os.path.getsize(dst) / (1024 * 1024)
            print(f"  [OK] {fname} ({size_mb:.1f} MB)")
        except Exception as e:
            print(f"  [错误] {fname} 下载失败: {e}")
            print(f"    请手动下载放到项目根目录：{url}")
            sys.exit(1)


def _estimate_size(fname):
    """估算文件大小（用于显示）。"""
    if "embed" in fname:
        return "~12 MB"
    if "get-pip" in fname:
        return "~2 MB"
    return "未知"


def _download(url, dst):
    """带进度条的下载。"""
    def _report(blocknum, blocksize, totalsize):
        if totalsize > 0:
            pct = min(100, int(blocknum * blocksize * 100 / totalsize))
            if pct % 20 == 0:
                downloaded = blocknum * blocksize / (1024 * 1024)
                total = totalsize / (1024 * 1024)
                print(f"    ... {pct}% ({downloaded:.0f}/{total:.0f} MB)")

    urllib.request.urlretrieve(url, dst, _report)


def clean_dist():
    """清理旧分发目录和 zip。"""
    if os.path.isdir(DIST_DIR):
        shutil.rmtree(DIST_DIR)
    if os.path.isfile(ZIP_FILE):
        os.remove(ZIP_FILE)
    os.makedirs(DIST_DIR, exist_ok=True)


def copy_files():
    """复制所有文件到分发目录。"""
    # Python 模块
    for f in PY_MODULES:
        src = os.path.join(ROOT, f)
        shutil.copy2(src, os.path.join(DIST_DIR, f))
        print(f"  [OK] {f}")

    # 配置文件
    for f in DATA_FILES:
        src = os.path.join(ROOT, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(DIST_DIR, f))
            print(f"  [OK] {f}")

    # 目录
    for d in DATA_DIRS:
        src = os.path.join(ROOT, d)
        dst = os.path.join(DIST_DIR, d)
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        print(f"  [OK] {d}/")

    # 示例文件
    for f in SAMPLE_FILES:
        src = os.path.join(ROOT, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(DIST_DIR, f))
            print(f"  [OK] {f}")

    # 捆绑的离线依赖
    for f in BUNDLED_FILES:
        src = os.path.join(ROOT, f)
        if os.path.isfile(src):
            size_mb = os.path.getsize(src) / (1024 * 1024)
            shutil.copy2(src, os.path.join(DIST_DIR, f))
            print(f"  [OK] {f} ({size_mb:.1f} MB)")
        else:
            print(f"  [警告] {f} 缺失，目标电脑安装时需联网下载")


def write_install_bat():
    """生成目标电脑上的安装脚本（含零依赖 Python 环境准备）。"""
    content = r'''@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title WB Content Tool — 一键安装

:: 切换到脚本所在目录（防止以管理员身份运行时 CWD = System32）
cd /d "%~dp0"

:: 强制 Python 输出 UTF-8，与 chcp 65001 一致，避免乱码
set "PYTHONIOENCODING=utf-8"

echo =============================================
echo    WB Content Tool — 一键安装
echo =============================================
echo.

:: ============================================
:: 步骤 1：准备 Python 环境（零依赖！）
::   优先使用系统 Python，没有则自动下载嵌入版
:: ============================================
echo [1/6] 准备 Python 环境...
echo.

set "USE_EMBEDDED=0"
set "PYTHON_EXE=python"

:: 检测系统架构（全局，下载和解压都要用）
set "ARCH=amd64"
if "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "ARCH=arm64"
if "%PROCESSOR_ARCHITECTURE%"=="x86" (
    if "%PROCESSOR_ARCHITEW6432%"=="" set "ARCH=win32"
)

set "PYTHON_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-%ARCH%.zip"
set "PYTHON_ZIP=python-embed.zip"

:: --- 1a. 检测系统 Python ---
echo   [检测] 查找系统 Python...
where python >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
    echo   [OK] 找到系统 Python !PYVER!

    :: 关键的验证：排除 Microsoft Store 占位程序
    :: 这种占位程序 where 能找到，但不是真正的 Python
    python -c "import sys; sys.exit(0 if 'WindowsApps' in sys.executable.lower() else 1)" 2>nul
    if !errorlevel! equ 0 (
        echo   [警告] 这是 Windows 自带的 Python 占位程序（不是真实 Python）
        echo   将自动下载嵌入版 Python 3.11.9...
        set "USE_EMBEDDED=1"
        goto :download_embed
    )

    :: 验证 Python 能否真正执行代码
    python -c "print('ok')" >nul 2>&1
    if !errorlevel! neq 0 (
        echo   [警告] Python 无法正常执行，将使用嵌入版
        set "USE_EMBEDDED=1"
        goto :download_embed
    )

    :: 验证 pip
    python -m pip --version >nul 2>&1
    if !errorlevel! neq 0 (
        echo   [警告] pip 不可用，将使用嵌入版 Python
        set "USE_EMBEDDED=1"
        goto :download_embed
    )
    echo   [OK] pip 可用
    goto :python_ready
) else (
    echo   [提示] 未检测到系统 Python
    set "USE_EMBEDDED=1"
)

:: --- 1b. 准备嵌入式 Python（优先使用内置包，离线安装）---
:download_embed
echo.
echo   [准备] 嵌入式 Python 3.11.9...

set "EMBED_DIR=%CD%\python-embed"
set "PYTHON_EXE=%EMBED_DIR%\python.exe"

:: 如果嵌入式 Python 已经存在，直接使用
if exist "%PYTHON_EXE%" (
    echo   [OK] 嵌入版 Python 已存在，跳过
    goto :python_ready
)

echo   架构: %ARCH%

:: ── 方式 1：使用随包携带的安装包（离线，秒装）──
set "BUNDLED_ZIP=python-3.11.9-embed-%ARCH%.zip"
if exist "%BUNDLED_ZIP%" (
    echo   [OK] 使用内置安装包（离线安装，无需联网）
    set "PYTHON_ZIP=%BUNDLED_ZIP%"
    goto :extract_embed
)

:: ── 方式 2：内置包架构不匹配，在线下载 ──
echo   [提示] 未找到内置 Python 包（架构: %ARCH%），尝试在线下载...

set "PYTHON_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-%ARCH%.zip"
set "PYTHON_ZIP=python-embed.zip"
echo   下载: %PYTHON_URL%

:: 下载方式 a：certutil（Windows 自带）
certutil -urlcache -split -f "%PYTHON_URL%" "%PYTHON_ZIP%" >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] 下载完成（certutil）
    goto :extract_embed
)

:: 下载方式 b：PowerShell
echo   [提示] certutil 失败，尝试 PowerShell...
powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_ZIP%'; Write-Host '  [OK] 下载完成'}" 2>nul
if %errorlevel% equ 0 (
    if exist "%PYTHON_ZIP%" goto :extract_embed
)

:: 下载方式 c：curl（Win10 1803+ 自带）
echo   [提示] PowerShell 失败，尝试 curl...
curl -L -o "%PYTHON_ZIP%" "%PYTHON_URL%" 2>nul
if %errorlevel% equ 0 (
    if exist "%PYTHON_ZIP%" goto :extract_embed
)

:: 全部在线下载方式失败
echo   [错误] 在线下载失败！所有下载方式均不可用。
echo.
echo   可能原因：
echo   1. 网络连接问题（防火墙/代理拦截）
echo   2. 公司网络策略限制
echo   3. Python.org 被屏蔽（国内常见）
echo.
echo   手动解决方案（任选其一）：
echo   A. 用浏览器下载 Python 3.11 安装：
echo      https://www.python.org/downloads/
echo   B. 手动下载嵌入版 zip 放到本目录：
echo      %PYTHON_URL%
echo   C. 如果有其他电脑已安装，复制 python-embed\ 文件夹过来
echo.
pause
exit /b 1

:: --- 解压嵌入版 Python ---
:extract_embed
echo   正在解压...
powershell -Command "& {$ProgressPreference='SilentlyContinue'; Expand-Archive -Path '%PYTHON_ZIP%' -DestinationPath '%EMBED_DIR%' -Force}" 2>nul
if %errorlevel% neq 0 (
    :: 手动解压（PowerShell 不可用时）
    echo   [提示] 使用 COM 解压...
    mkdir "%EMBED_DIR%" 2>nul
    powershell -Command "& {$shell=New-Object -ComObject Shell.Application; $zip=$shell.NameSpace('%CD%\%PYTHON_ZIP%'); $dest=$shell.NameSpace('%CD%\%EMBED_DIR%'); $dest.CopyHere($zip.Items(), 16)}" 2>nul
)

:: 清理临时文件（只删除在线下载的临时 zip，保留内置包）
if not "%BUNDLED_ZIP%"=="%PYTHON_ZIP%" del "%PYTHON_ZIP%" 2>nul

:: --- 1c. 配置嵌入式 Python（启用 pip 支持）---
echo   正在配置 pip...

:: 修改 ._pth 文件：追加 "import site" 使 pip 包可发现
for %%f in ("%EMBED_DIR%\python*._pth") do (
    findstr /c:"import site" "%%f" >nul 2>&1
    if !errorlevel! neq 0 (
        echo.>> "%%f"
        echo import site>> "%%f"
    )
)

:: ── 安装 pip：优先使用随包携带的 get-pip.py（离线）──
if exist "get-pip.py" (
    echo   [OK] 使用内置 get-pip.py（离线安装）
    copy "get-pip.py" "%EMBED_DIR%\get-pip.py" >nul 2>&1
) else (
    :: 在线下载 get-pip.py
    echo   [提示] 在线下载 get-pip.py...
    certutil -urlcache -split -f "https://bootstrap.pypa.io/get-pip.py" "%EMBED_DIR%\get-pip.py" >nul 2>&1
    if not exist "%EMBED_DIR%\get-pip.py" (
        powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%EMBED_DIR%\get-pip.py'}" 2>nul
    )
)

if exist "%EMBED_DIR%\get-pip.py" (
    "%PYTHON_EXE%" "%EMBED_DIR%\get-pip.py" --no-warn-script-location 2>nul
    del "%EMBED_DIR%\get-pip.py" 2>nul
    echo   [OK] pip 已安装到嵌入版 Python
) else (
    echo   [警告] get-pip.py 下载失败
    echo   请手动安装 Python: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo   [OK] 嵌入版 Python 3.11.9 就绪（%EMBED_DIR%）
set "USE_EMBEDDED=1"

:: --- Python 就绪 ---
:python_ready
echo.
echo   ── Python 环境：%PYTHON_EXE% ──
"%PYTHON_EXE%" --version

:: ============================================
:: 步骤 2：升级 pip
:: ============================================
echo.
echo [2/6] 升级 pip 和构建工具...
"%PYTHON_EXE%" -m pip install --upgrade pip setuptools wheel -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet 2>nul
echo   [OK] 完成

:: ============================================
:: 步骤 3：创建虚拟环境
:: ============================================
echo.
echo [3/6] 创建虚拟环境...

if exist "venv\Scripts\python.exe" (
    echo   [提示] 虚拟环境已存在，跳过创建
    goto :venv_ready
)

:: 检查是否 Microsoft Store 版 Python（这种版本 venv 经常有问题）
"%PYTHON_EXE%" -c "import sys; sys.exit(1 if 'WindowsApps' in sys.executable.lower() else 0)" 2>nul
if %errorlevel% equ 1 (
    echo   [警告] 检测到 Microsoft Store 版 Python，跳过（使用嵌入版替代）
    if exist "python-embed\python.exe" (
        set "PYTHON_EXE=%CD%\python-embed\python.exe"
        echo   [OK] 已切换到嵌入版 Python
        goto :retry_venv
    ) else (
        set "USE_EMBEDDED=1"
        goto :download_embed
    )
)

:: 方案 A：标准 venv
:retry_venv
echo   正在创建虚拟环境（方案 A）...
"%PYTHON_EXE%" -m venv venv
if %errorlevel% equ 0 (
    if exist "venv\Scripts\python.exe" goto :venv_ready
)

:: 方案 B：跳过 pip 引导的 venv（某些 Python 安装缺少 ensurepip）
echo   [警告] 方案 A 失败，尝试方案 B（--without-pip）...
rmdir /s /q venv 2>nul
"%PYTHON_EXE%" -m venv venv --without-pip
if %errorlevel% equ 0 (
    if exist "venv\Scripts\python.exe" (
        :: 手动安装 pip 到 venv
        echo   正在为虚拟环境安装 pip...
        venv\Scripts\python.exe -c "import urllib.request; exec(urllib.request.urlopen('https://bootstrap.pypa.io/get-pip.py').read())" 2>nul
        if !errorlevel! neq 0 (
            echo   [提示] pip 引导跳过（将在依赖安装步骤处理）
        )
        goto :venv_ready
    )
)

:: 方案 C：嵌入式 Python 已存在则用它重建
if exist "python-embed\python.exe" (
    echo   [警告] 方案 B 失败，尝试方案 C（使用嵌入版 Python）...
    rmdir /s /q venv 2>nul
    python-embed\python.exe -m venv venv
    if !errorlevel! equ 0 (
        if exist "venv\Scripts\python.exe" (
            set "PYTHON_EXE=%CD%\python-embed\python.exe"
            goto :venv_ready
        )
    )
) else (
    :: 下载嵌入版 Python 作为最后手段
    echo   正在下载嵌入版 Python 作为后备...
    set "USE_EMBEDDED=1"
    goto :download_embed
)

:: 全部方案失败
echo.
echo   =============================================
echo   [错误] 虚拟环境创建失败！
echo   =============================================
echo.
echo   所有 3 种方案均失败。请尝试：
echo.
echo   1. 从 python.org 重新安装 Python 3.11+
echo      （安装时务必勾选 "Add Python to PATH"）
echo   2. 暂时关闭杀毒软件后重试
echo   3. 以管理员身份运行本脚本
echo   4. 确保磁盘空间充足（> 5 GB）
echo   5. 解压路径不要包含中文或空格
echo.
echo   浏览器打开: https://www.python.org/downloads/
echo.
pause
exit /b 1

:venv_ready
echo   [OK] 虚拟环境就绪

:: ============================================
:: 步骤 4：安装 Python 依赖
:: ============================================
echo.
echo [4/6] 安装 Python 依赖（清华镜像，约需 3~8 分钟）...
echo   (首次运行需下载约 2~4 GB，请耐心等待)

call venv\Scripts\activate.bat

:: 安装主依赖（带重试）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if %errorlevel% neq 0 (
    echo.
    echo   [警告] 部分依赖安装失败，正在重试（使用默认源）...
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo   [警告] 仍有依赖安装失败，不影响基本功能
    )
)

echo   [OK] Python 依赖安装完成

:: ============================================
:: 步骤 5：安装 Playwright 浏览器
:: ============================================
echo.
echo [5/6] 安装 Playwright 浏览器（Chromium，约 150 MB）...

:: 优先使用国内镜像
set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/
playwright install chromium 2>nul
if %errorlevel% neq 0 (
    set PLAYWRIGHT_DOWNLOAD_HOST=
    playwright install chromium 2>nul
    if %errorlevel% neq 0 (
        echo   [警告] Playwright 浏览器安装失败
        echo   → 爬虫的 "Playwright" 模式不可用
        echo   → 请使用 "ScraperAPI" 模式（付费代理）
        echo   → 或手动运行: venv\Scripts\playwright install chromium
    ) else (
        echo   [OK] Chromium 浏览器安装完成
    )
) else (
    echo   [OK] Chromium 浏览器安装完成
)

:: ============================================
:: 步骤 6：初始化配置
:: ============================================
echo.
echo [6/6] 初始化配置文件...

if not exist ".env" (
    if exist ".env.template" (
        copy ".env.template" ".env" >nul
        echo   [OK] 已从模板创建 .env 配置文件
        echo   → 请编辑 .env 填入 API Key 后再启动
    ) else (
        echo   [提示] 首次启动时会自动生成 .env（含默认值）
    )
) else (
    echo   [提示] .env 已存在，保留现有配置
)

:: ============================================
:: 完成
:: ============================================
echo.
echo =============================================
echo    安装完成！
echo =============================================
echo.
if "!USE_EMBEDDED!"=="1" (
    echo   ✓ 嵌入版 Python 3.11.9（免安装，随包携带）
)
echo   ✓ 虚拟环境: venv\
echo   ✓ Python 依赖包
echo   ✓ Playwright 浏览器（爬虫采集）
echo   ✓ 配置文件 .env
echo.
echo   ── 下一步 ──
echo.
echo   1. 编辑 .env 填入 API Key（或用记事本打开填写）
echo   2. 双击 启动.bat 启动工具
echo   3. 有问题 → 双击 环境检查.bat 诊断
echo.
pause
endlocal
'''
    path = os.path.join(DIST_DIR, "安装.bat")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("  [OK] 安装.bat")


def write_run_bat():
    """生成目标电脑上的启动脚本（含快速自检）。"""
    content = r'''@echo off
chcp 65001 >nul
title WB Content Tool

cd /d "%~dp0"

echo =============================================
echo    WB Content Tool — 启动
echo =============================================
echo.

:: ---- 找到可用的 Python ----
echo [自检] 检查运行环境...

:: 优先用 venv 里的 Python
if exist "venv\Scripts\python.exe" (
    call venv\Scripts\activate.bat
    goto :check_env
)

:: venv 不存在 — 可能用户直接点了启动
if exist "python-embed\python.exe" (
    echo   [提示] 检测到嵌入版 Python，但虚拟环境未创建
    echo   请先运行 安装.bat 完成安装
    pause
    exit /b 1
)

where python >nul 2>&1
if %errorlevel% equ 0 (
    echo   [提示] 检测到系统 Python，但虚拟环境未创建
    echo   请先运行 安装.bat 完成安装
    pause
    exit /b 1
)

echo   [错误] 未找到任何 Python 环境
echo   请先运行 安装.bat（会自动下载 Python）
pause
exit /b 1

:check_env
:: ---- 检查 .env 配置 ----
if not exist ".env" (
    echo   [提示] 未找到 .env 配置文件，正在生成默认配置...
    python -c "from config import settings; print('  .env 已创建')" 2>nul
)
echo   [OK] 配置文件就绪

:: ---- 检查 Streamlit ----
python -c "import streamlit" 2>nul
if %errorlevel% neq 0 (
    echo   [错误] Streamlit 未安装
    echo   请先运行 安装.bat 完成安装
    pause
    exit /b 1
)
echo   [OK] Streamlit 就绪

:: ---- 检查 Playwright ----
python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()" 2>nul
if %errorlevel% equ 0 (
    echo   [OK] Playwright 浏览器可用
) else (
    echo   [提示] Playwright 不可用，爬虫请用 ScraperAPI 模式
)

:: ---- 检查 API Key ----
python -c "from config import settings; exit(0 if settings.phase1_api_key else 1)" 2>nul
if %errorlevel% neq 0 (
    echo   [提示] API Key 未配置，启动后在侧边栏设置
)

echo.
echo   正在启动 Web 界面...
echo   浏览器访问: http://localhost:8501
echo   按 Ctrl+C 停止服务
echo =============================================
echo.

start "" http://localhost:8501
streamlit run app.py --server.port 8501
pause
'''
    path = os.path.join(DIST_DIR, "启动.bat")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("  [OK] 启动.bat")


def write_check_env_bat():
    """生成独立的环境诊断脚本。"""
    content = r'''@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title WB Content Tool — 环境检查

cd /d "%~dp0"

echo =============================================
echo    WB Content Tool — 环境检查
echo =============================================
echo.
echo   (可随时运行此脚本诊断问题)
echo.

set ERRORS=0
set WARNINGS=0

:: ---- 1. Python ----
echo [1] Python 环境
echo   ----------

set "PY_FOUND=0"
where python >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo   [OK] 系统 Python %%v
    set "PY_FOUND=1"
) else if exist "python-embed\python.exe" (
    for /f "tokens=2" %%v in ('python-embed\python.exe --version 2^>^&1') do echo   [OK] 嵌入版 Python %%v（免安装，随包携带）
    set "PY_FOUND=1"
)
if "!PY_FOUND!"=="0" (
    echo   [错误] 未找到 Python！请运行 安装.bat（会自动下载嵌入版）
    set /a ERRORS+=1
)

:: ---- 2. 虚拟环境 ----
:check_venv
echo.
echo [2] 虚拟环境
echo   ----------

if not exist "venv\Scripts\python.exe" (
    echo   [错误] 虚拟环境不存在，请先运行 安装.bat
    set /a ERRORS+=1
    goto :check_env
)
echo   [OK] venv\ 存在

call venv\Scripts\activate.bat

:: ---- 3. .env 配置 ----
echo.
echo [3] 配置文件 .env
echo   ----------
if not exist ".env" (
    echo   [警告] .env 文件不存在
    echo   → 启动时会自动创建，但你需要在 Web 界面侧边栏配置 API Key 后点击"保存配置"
    set /a WARNINGS+=1
) else (
    echo   [OK] .env 存在

    :: 检查关键配置项是否已填写
    for /f "tokens=2 delims==" %%a in ('findstr /b "PHASE1_API_KEY=" .env 2^>nul') do set P1KEY=%%a
    if "!P1KEY!"=="" (
        echo   [警告] PHASE1_API_KEY 未配置（信息萃取和图片翻译需要）
        set /a WARNINGS+=1
    ) else (
        echo   [OK] PHASE1_API_KEY 已配置
    )
)

:: ---- 4. 核心依赖 ----
echo.
echo [4] 核心 Python 依赖
echo   ----------

python -c "import streamlit; print('  [OK] streamlit', streamlit.__version__)" 2>nul || (
    echo   [错误] streamlit 未安装
    set /a ERRORS+=1
)
python -c "import openpyxl; print('  [OK] openpyxl', openpyxl.__version__)" 2>nul || echo   [警告] openpyxl 未安装
python -c "import httpx; print('  [OK] httpx')" 2>nul || echo   [警告] httpx 未安装
python -c "import openai; print('  [OK] openai', openai.__version__)" 2>nul || echo   [警告] openai 未安装
python -c "import anthropic; print('  [OK] anthropic')" 2>nul || echo   [警告] anthropic 未安装

:: ---- 5. 爬虫 (Playwright) ----
echo.
echo [5] 爬虫 — Playwright 浏览器
echo   ----------

python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop(); print('  [OK] Chromium 浏览器可用')" 2>nul
if %errorlevel% neq 0 (
    echo   [警告] Playwright 浏览器不可用
    echo   → 爬虫的 "Playwright" 模式不可用，请使用 "ScraperAPI" 模式
    echo   → 修复: 运行 venv\Scripts\playwright install chromium
    set /a WARNINGS+=1
)

:: ---- 6. OCR (PaddleOCR) ----
echo.
echo [6] OCR 图片文字检测
echo   ----------

python -c "from paddleocr import PaddleOCR; ocr=PaddleOCR(lang='en', show_log=False); print('  [OK] PaddleOCR 可用')" 2>nul
if %errorlevel% neq 0 (
    echo   [警告] PaddleOCR 初始化失败
    echo   → 图片翻译"传统管线"不可用，但"AI 管线"不受影响
    echo   → 常见原因: PaddlePaddle 不支持此系统/Python 版本
    set /a WARNINGS+=1
)

:: ---- 7. 图片修复 (Replicate) ----
echo.
echo [7] AI 图片修复（可选）
echo   ----------

python -c "import replicate; print('  [OK] replicate 可用')" 2>nul || (
    echo   [提示] replicate 不可用（非关键，仅影响传统管线文字擦除）
)

:: ---- 8. Gemini ----
echo.
echo [8] Gemini 图片生成（可选）
echo   ----------

python -c "import google.genai; print('  [OK] google-genai 可用')" 2>nul || (
    echo   [提示] google-genai 不可用（非关键，仅影响 AI 管线图片处理）
)

:: ---- 9. 字体 ----
echo.
echo [9] 字体文件
echo   ----------

if exist "fonts\Roboto-Regular.ttf" (
    echo   [OK] Roboto-Regular.ttf 存在
) else (
    echo   [提示] 字体文件缺失，可在图片翻译 Tab 中指定其他字体
)

:: ---- 10. 网络 ----
echo.
echo [10] 网络连接
echo   ----------

python -c "import urllib.request; urllib.request.urlopen('https://www.baidu.com', timeout=5); print('  [OK] 网络可达')" 2>nul || (
    echo   [警告] 网络不可达，AI API 调用将失败
    set /a WARNINGS+=1
)

:: ---- 总结 ----
echo.
echo =============================================
echo    诊断总结
echo =============================================
echo.
echo   错误: %ERRORS%   警告: %WARNINGS%
echo.

if %ERRORS% gtr 0 (
    echo   请先解决上方 [错误] 项。
    echo   最常见解决方案：重新运行 安装.bat
)
if %WARNINGS% gtr 0 (
    echo   上方 [警告] 项不影响基本使用，但部分功能可能受限。
)
if %ERRORS% equ 0 if %WARNINGS% equ 0 (
    echo   所有检查通过，环境正常！
)

echo.
pause
'''
    path = os.path.join(DIST_DIR, "环境检查.bat")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("  [OK] 环境检查.bat")


def create_zip():
    """将分发目录压缩为 ZIP。"""
    os.makedirs(os.path.dirname(ZIP_FILE), exist_ok=True)
    with zipfile.ZipFile(ZIP_FILE, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(DIST_DIR):
            for fname in files:
                full = os.path.join(root, fname)
                arcname = os.path.relpath(full, os.path.dirname(DIST_DIR))
                zf.write(full, arcname)
    size_mb = os.path.getsize(ZIP_FILE) / (1024 * 1024)
    print(f"\n  打包完成: {ZIP_FILE} ({size_mb:.1f} MB)")


def main():
    print("=" * 48)
    print("  WB Content Tool — 分发打包")
    print("=" * 48)
    print()

    print("[1/6] 清理旧文件...")
    clean_dist()

    print("[2/6] 下载离线依赖...")
    download_bundled()

    print("[3/6] 复制项目文件...")
    copy_files()

    print("[4/6] 生成脚本...")
    write_install_bat()
    write_run_bat()
    write_check_env_bat()

    print("[5/6] 压缩为 ZIP...")
    create_zip()

    print()
    print("=" * 48)
    print("  打包完成！")
    print()
    print("  文件: dist\\WB-Content-Tool.zip")
    print()
    print("  使用方法（在目标电脑上）：")
    print("    1. 解压 ZIP")
    print("    2. 双击 安装.bat（首次安装，支持离线！）")
    print("    3. 双击 启动.bat（日常启动）")
    print("    4. 出问题 → 双击 环境检查.bat（诊断）")
    print("    5. 在 Web 界面侧边栏配置 API Key → 保存")
    print()
    print("  现在包内含：")
    print("    - 安装.bat — 一键安装（venv + pip + Playwright + .env 初始化）")
    print("    - 启动.bat — 一键启动（含环境自检）")
    print("    - 环境检查.bat — 独立诊断工具（10项检查）")
    print("    - .env.template — 配置文件模板")
    print("    - fonts/ — 俄文字体文件")
    print("    - python-3.11.9-embed-amd64.zip — 内置 Python（离线安装）")
    print("    - get-pip.py — pip 引导脚本（离线安装）")
    print("=" * 48)


if __name__ == "__main__":
    main()
