"""
测试 prompts/image_translation_persona.txt 文件存在且内容正确。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


_PERSONA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "prompts",
    "image_translation_persona.txt",
)


class TestImageTranslationPersona:
    """图片翻译人设 Prompt 文件。"""

    def test_file_exists(self):
        """验证 prompts/image_translation_persona.txt 文件存在"""
        assert os.path.isfile(_PERSONA_PATH), (
            f"图片翻译人设文件不存在: {_PERSONA_PATH}"
        )

    def test_file_is_non_empty(self):
        """验证文件内容非空"""
        if not os.path.isfile(_PERSONA_PATH):
            pytest.skip("文件不存在")
        with open(_PERSONA_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        assert len(content.strip()) > 0, "Prompt 文件内容为空"

    def test_contains_translation_rules(self):
        """验证包含核心翻译规则关键词"""
        if not os.path.isfile(_PERSONA_PATH):
            pytest.skip("文件不存在")
        with open(_PERSONA_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        assert "翻译" in content, "应包含「翻译」"
        assert "俄语" in content, "应包含「俄语」"
        assert "直译" in content, "应包含「直译」"

    def test_contains_output_format(self):
        """验证包含 JSON 输出格式指令"""
        if not os.path.isfile(_PERSONA_PATH):
            pytest.skip("文件不存在")
        with open(_PERSONA_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        assert "translations" in content, "应包含 translations 数组格式"
