"""
WB Content Tool — 分发打包脚本。

将项目核心文件复制到 dist/WB-Content-Tool/ 并打包为 ZIP。
排除开发/测试文件、缓存、敏感配置等。
"""

import os
import shutil
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(ROOT, "dist", "WB-Content-Tool")
ZIP_FILE = os.path.join(ROOT, "dist", "WB-Content-Tool.zip")

# 要包含的 Python 模块
PY_MODULES = [
    "app.py",
    "config.py",
    "crawler.py",
    "crawler_ui.py",
    "excel_io.py",
    "extractor.py",
    "translator.py",
    "translator_ui.py",
]

# 要包含的配置/数据文件
DATA_FILES = [
    "requirements.txt",
    ".env.template",
    "README.md",
]

# 要包含的目录
DATA_DIRS = [
    "prompts",
    ".streamlit",
]

# 示例文件
SAMPLE_FILES = [
    "案例 asin（采集用）.xlsx",
]


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


def write_install_bat():
    """生成目标电脑上的安装脚本。"""
    content = r'''@echo off
chcp 65001 >nul
title WB Content Tool — 安装

echo ========================================
echo   WB Content Tool — 一键安装
echo ========================================
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python！
    echo.
    echo 请先安装 Python 3.12+：
    echo   https://www.python.org/downloads/
    echo.
    echo ** 安装时务必勾选 "Add Python to PATH" **
    pause
    exit /b 1
)

echo [1/3] 创建虚拟环境...
python -m venv venv

echo [2/3] 安装 Python 依赖（使用清华镜像加速）...
call venv\Scripts\activate.bat
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

echo [3/3] 初始化配置文件...
if not exist ".env" (
    copy .env.template .env >nul
    echo   [提示] 已从模板创建 .env 文件
)

echo.
echo ========================================
echo   安装完成！
echo ========================================
echo.
echo 下一步：
echo   1. 编辑 .env 文件，填入你的 API Key
echo      需要填写的：TRANSLATE_API_KEY、SCRAPERAPI_KEY
echo   2. 双击 启动.bat 即可运行
echo.
pause
'''
    path = os.path.join(DIST_DIR, "安装.bat")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("  [OK] 安装.bat")


def write_run_bat():
    """生成目标电脑上的启动脚本。"""
    content = r'''@echo off
chcp 65001 >nul
title WB Content Tool

cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境，请先运行 安装.bat
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
streamlit run app.py --server.port 8501
pause
'''
    path = os.path.join(DIST_DIR, "启动.bat")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("  [OK] 启动.bat")


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

    print("[1/4] 清理旧文件...")
    clean_dist()

    print("[2/4] 复制项目文件...")
    copy_files()

    print("[3/4] 生成脚本...")
    write_install_bat()
    write_run_bat()

    print("[4/4] 压缩为 ZIP...")
    create_zip()

    print()
    print("=" * 48)
    print("  打包完成！")
    print()
    print("  文件: dist\\WB-Content-Tool.zip")
    print()
    print("  使用方法（在目标电脑上）：")
    print("    1. 解压 ZIP")
    print("    2. 双击 安装.bat")
    print("    3. 编辑 .env 填入 API Key")
    print("    4. 双击 启动.bat")
    print("=" * 48)


if __name__ == "__main__":
    main()
