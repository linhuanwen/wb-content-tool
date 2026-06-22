"""
测试 .env.template：配置模板文件。

该文件供用户复制为 .env 后填入真实值。测试验证其包含所有必需的配置项
和说明注释。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENV_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), ".env.template"
)

# 所有必需的配置项 key
REQUIRED_KEYS = [
    "TRANSLATE_API_PROVIDER",
    "TRANSLATE_API_KEY",
    "TRANSLATE_API_BASE_URL",
    "TRANSLATE_MODEL",
    "CRAWLER_MODE",
    "CRAWLER_HEADLESS",
    "CRAWLER_DELAY",
    "SCRAPERAPI_KEY",
]


def _load_env_template() -> str:
    """加载 .env.template 文件内容。"""
    with open(ENV_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        return f.read()


class TestEnvTemplateExists:
    """验证 .env.template 文件存在。"""

    def test_file_exists(self):
        """.env.template 应存在于项目根目录"""
        assert os.path.isfile(ENV_TEMPLATE_PATH), (
            f".env.template 不存在于 {ENV_TEMPLATE_PATH}"
        )


class TestEnvTemplateKeys:
    """验证 .env.template 包含所有必需的配置项。"""

    def test_all_required_keys_present(self):
        """应包含全部 8 个配置项 key"""
        content = _load_env_template()
        for key in REQUIRED_KEYS:
            assert key in content, f".env.template 缺少配置项: {key}"

    def test_keys_are_commented_or_assigned(self):
        """
        每个 key 要么在说明注释中出现，要么有赋值（= 号）。
        配置项应让用户自行取消注释并填入值。
        """
        content = _load_env_template()
        for key in REQUIRED_KEYS:
            assert f"{key}=" in content or f"# {key}" in content, (
                f"{key} 必须出现在 .env.template 中（赋值或注释说明）"
            )

    def test_has_comments_explaining_each_key(self):
        """.env.template 应为每个配置项提供中文注释说明"""
        content = _load_env_template()
        lines_with_hash = [l for l in content.split("\n") if l.strip().startswith("#")]
        # 至少要有 8 条注释（每个配置项一条说明）
        assert len(lines_with_hash) >= 8, (
            f"期望至少 8 条注释说明，实际只有 {len(lines_with_hash)} 条"
        )
