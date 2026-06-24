"""
测试 prompts/translation_persona.txt：翻译 AI 人设 System Prompt。

该文件是翻译模块的核心资产，定义了 AI 的角色、技能、工作流和输出格式。
测试通过读取文件内容来验证其完整性，而非测试实现细节。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


PROMPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "prompts", "translation_persona.txt"
)


def _load_prompt() -> str:
    """加载 prompt 文件内容的辅助函数。"""
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


class TestPersonaFileExists:
    """验证 prompt 文件存在且可读。"""

    def test_file_exists(self):
        """prompt 文件应该存在于 prompts/ 目录下"""
        assert os.path.isfile(PROMPT_PATH), (
            f"translation_persona.txt 不存在于 {PROMPT_PATH}"
        )

    def test_file_is_not_empty(self):
        """prompt 文件内容不应为空"""
        content = _load_prompt()
        assert len(content.strip()) > 0, "translation_persona.txt 内容为空"


class TestPersonaContent:
    """验证 prompt 包含从 docx 提取的核心人设内容。"""

    def test_contains_role_definition(self):
        """应定义 AI 角色为 Wildberries 俄语电商文案优化师"""
        content = _load_prompt()
        assert "Wildberries 俄语电商文案优化师" in content, "缺少角色定义"

    def test_contains_wildberries_reference(self):
        """应提及目标平台 Wildberries"""
        content = _load_prompt()
        assert "Wildberries" in content, "缺少 Wildberries 平台引用"

    def test_contains_spider_web_keyword_strategy(self):
        """应包含蜘蛛网关键词布局策略"""
        content = _load_prompt()
        assert "蜘蛛网" in content, "缺少蜘蛛网关键词布局策略"
        assert "核心区" in content, "缺少核心区说明"
        assert "说服区" in content, "缺少说服区说明"
        assert "补充区" in content, "缺少补充区说明"

    def test_contains_seo_instruction(self):
        """应包含 SEO 优化相关指令"""
        content = _load_prompt()
        assert "SEO" in content or "搜索" in content, "缺少 SEO/搜索优化指令"

    def test_contains_russian_market_requirement(self):
        """应包含俄罗斯市场本地化要求（小写、无特殊符号）"""
        content = _load_prompt()
        assert "小写" in content, "缺少小写要求"
        assert "特殊符号" in content, "缺少无特殊符号要求"


class TestJsonFormatInstruction:
    """验证末尾包含 JSON 输出格式指令。"""

    def test_contains_json_format_section(self):
        """prompt 末尾必须包含 JSON 输出格式指令块"""
        content = _load_prompt()
        assert "### JSON 输出格式" in content, (
            "缺少 JSON 输出格式指令块（### JSON 输出格式）"
        )

    def test_json_schema_defines_required_fields(self):
        """JSON 格式应定义 russian_title、core_keywords、russian_description 三个字段"""
        content = _load_prompt()
        assert "russian_title" in content, "JSON 格式缺少 russian_title 字段"
        assert "core_keywords" in content, "JSON 格式缺少 core_keywords 字段"
        assert "russian_description" in content, "JSON 格式缺少 russian_description 字段"

    def test_json_section_is_at_end(self):
        """JSON 格式指令应位于文件末尾"""
        content = _load_prompt()
        json_marker = "### JSON 输出格式"
        idx = content.rfind(json_marker)
        assert idx != -1, "找不到 JSON 输出格式标记"
        # marker 之后的内容不应超过文件剩余部分的 80%（即 JSON 部分在末尾）
        after_marker = content[idx:]
        assert len(after_marker) < len(content) * 0.6, (
            "JSON 格式指令不应占据文件大部分内容，应附加在末尾"
        )
