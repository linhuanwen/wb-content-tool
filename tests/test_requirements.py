"""
测试 requirements.txt：Python 依赖清单。

验证文件包含项目所需的所有依赖包，格式符合 pip 规范。
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REQUIREMENTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "requirements.txt"
)

# Issue #1 列出的必需依赖
REQUIRED_PACKAGES = [
    "streamlit",
    "playwright",
    "openpyxl",
    "pandas",
    "openai",
    "anthropic",
    "python-dotenv",
    "pydantic",
    "beautifulsoup4",
    "lxml",
]

# 测试所需依赖
DEV_PACKAGES = [
    "pytest",
    "pytest-asyncio",
]


def _load_requirements() -> list[str]:
    """解析 requirements.txt 返回包名列表。"""
    with open(REQUIREMENTS_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    packages = []
    for line in lines:
        stripped = line.strip()
        # 跳过空行和注释行
        if not stripped or stripped.startswith("#"):
            continue
        # 提取包名：处理 package>=1.0, package==1.0, package~=1.0 等格式
        match = re.match(r"^([a-zA-Z0-9_\-\.]+)", stripped)
        if match:
            packages.append(match.group(1).lower())
    return packages


class TestRequirementsExists:
    """验证 requirements.txt 文件存在且可读。"""

    def test_file_exists(self):
        """requirements.txt 应存在于项目根目录"""
        assert os.path.isfile(REQUIREMENTS_PATH), (
            f"requirements.txt 不存在于 {REQUIREMENTS_PATH}"
        )

    def test_file_is_not_empty(self):
        """requirements.txt 不应为空"""
        packages = _load_requirements()
        assert len(packages) > 0, "requirements.txt 为空"


class TestRequiredPackages:
    """验证包含所有必需的依赖包。"""

    def test_all_required_packages_present(self):
        """应包含 Issue #1 指定的 8 个核心依赖"""
        packages = _load_requirements()
        for pkg in REQUIRED_PACKAGES:
            assert pkg in packages, f"requirements.txt 缺少依赖: {pkg}"

    def test_dev_packages_present(self):
        """应包含测试依赖 pytest"""
        packages = _load_requirements()
        for pkg in DEV_PACKAGES:
            assert pkg in packages, f"requirements.txt 缺少开发依赖: {pkg}"


class TestRequirementsFormat:
    """验证 requirements.txt 格式符合 pip 规范。"""

    def test_each_line_is_valid_format(self):
        """每行应为有效的 pip 依赖声明（包名 或 包名==版本）"""
        with open(REQUIREMENTS_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # 有效的 pip 格式：package_name 或 package_name==version 等
            is_valid = re.match(r"^[a-zA-Z0-9_\-\.]+(\s*[><=!~]+\s*[\w\.\*]+)?(\s*;\s*.+)?$", stripped)
            assert is_valid is not None, (
                f"第 {i} 行格式无效: '{stripped}'"
            )
