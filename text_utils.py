"""
文案处理公共工具 — 跨模块共享的文本后处理函数和正则。

提取自 translator.py 和 phase2_translator.py 中的重复代码。
"""

import re

# ── 标题后处理 ──────────────────────────────────────────────

# 标题最大字符数
_MAX_TITLE_LENGTH = 60

# 标题中允许保留的字符模式：西里尔字母、拉丁字母、数字、空格
_TITLE_ALLOWED_CHARS = re.compile(r"[^a-zA-Zа-яА-ЯёЁ0-9\s]")


def post_process_title(title: str) -> str:
    """对 AI 生成的俄语标题进行后处理。

    规则：
    1. 全小写
    2. 去除特殊符号（保留字母、数字、空格）
    3. 截断到 60 字符以内
    4. 去除首尾空格

    Args:
        title: AI 生成的原始俄语标题。

    Returns:
        处理后的标题字符串。
    """
    if not title:
        return ""

    # 去特殊符号 → 小写 → 去首尾空格
    cleaned = _TITLE_ALLOWED_CHARS.sub("", title)
    lowered = cleaned.lower()
    stripped = lowered.strip()

    # 截断到 60 字符
    if len(stripped) > _MAX_TITLE_LENGTH:
        stripped = stripped[:_MAX_TITLE_LENGTH]

    # 截断后再次去除可能的首尾空格
    return stripped.strip()


# ── JSON 解析 ──────────────────────────────────────────────

# Markdown 代码块 JSON 提取正则
MARKDOWN_JSON_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def parse_json_response(text: str, required_fields: set[str] | None = None) -> dict:
    """从 AI 响应文本中解析 JSON。

    支持：
    1. 直接 JSON 解析
    2. 从 Markdown 代码块（```json ... ```）中提取

    Args:
        text: AI 返回的原始响应文本。
        required_fields: 必须存在的字段集合。为 None 时不校验。

    Returns:
        解析后的字典。

    Raises:
        ValueError: 无法解析 JSON、或缺少必要字段时抛出。
    """
    import json

    if not text or not text.strip():
        raise ValueError("AI 返回空响应，无法解析 JSON")

    parsed: dict | None = None

    # 尝试 1：直接解析
    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # 尝试 2：从 Markdown 代码块提取
    if parsed is None:
        match = MARKDOWN_JSON_RE.search(text)
        if match:
            try:
                parsed = json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

    if parsed is None:
        raise ValueError(
            f"无法解析 AI 响应为 JSON。"
            f"响应前 200 字符: {text.strip()[:200]}"
        )

    if not isinstance(parsed, dict):
        raise ValueError(
            f"AI 返回的 JSON 不是对象类型: {type(parsed).__name__}"
        )

    # 验证必要字段
    if required_fields:
        missing = required_fields - set(parsed.keys())
        if missing:
            raise ValueError(
                f"AI 返回的 JSON 缺少必要字段: {', '.join(sorted(missing))}"
            )

    return parsed
