"""
AI 翻译模块 — 调用 AI API 将英文产品文案转换为俄语 SEO 优化文案。

公共接口：
    TranslationProvider   — 抽象基类
    OpenAICompatibleProvider — OpenAI 兼容协议实现
    ClaudeProvider        — Anthropic Claude 实现
    create_translation_provider — 工厂函数
    translate_batch       — 批量翻译入口
"""

import argparse
import json
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path


# ============================================================
# System Prompt 加载
# ============================================================

# prompts 目录下的翻译人设文件路径
_PERSONA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "prompts", "translation_persona.txt"
)


def _load_system_prompt() -> str:
    """加载翻译人设 System Prompt 文件。

    Returns:
        prompts/translation_persona.txt 的完整内容。

    Raises:
        FileNotFoundError: 文件不存在时抛出。
    """
    path = Path(_PERSONA_PATH)
    if not path.is_file():
        raise FileNotFoundError(
            f"翻译人设文件不存在: {_PERSONA_PATH}\n"
            f"请确保 prompts/translation_persona.txt 文件存在。"
        )
    return path.read_text(encoding="utf-8")


# 标题最大字符数
_MAX_TITLE_LENGTH = 60

# 标题中允许保留的字符模式：西里尔字母、拉丁字母、数字、空格
_TITLE_ALLOWED_CHARS = re.compile(r"[^a-zA-Zа-яА-ЯёЁ0-9\s]")


def _post_process_title(title: str) -> str:
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


# AI 响应 JSON 中必须包含的字段
_REQUIRED_JSON_FIELDS = {"russian_title", "core_keywords", "russian_description"}

# Markdown 代码块 JSON 提取正则
_MARKDOWN_JSON_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def _parse_ai_response(text: str) -> dict:
    """从 AI 响应文本中解析 JSON。

    支持：
    1. 直接 JSON 解析
    2. 从 Markdown 代码块（```json ... ```）中提取

    Args:
        text: AI 返回的原始响应文本。

    Returns:
        解析后的字典，包含 russian_title, core_keywords, russian_description。

    Raises:
        ValueError: 无法解析 JSON、或缺少必要字段时抛出。
    """
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
        match = _MARKDOWN_JSON_RE.search(text)
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
        raise ValueError(f"AI 返回的 JSON 不是对象类型: {type(parsed).__name__}")

    # 验证必要字段
    missing = _REQUIRED_JSON_FIELDS - set(parsed.keys())
    if missing:
        raise ValueError(
            f"AI 返回的 JSON 缺少必要字段: {', '.join(sorted(missing))}"
        )

    return parsed


# ============================================================
# 抽象接口
# ============================================================

class TranslationProvider(ABC):
    """AI 翻译服务抽象基类。

    子类只需实现 _call_api 方法。translate 方法提供统一的
    解析、重试、后处理逻辑。
    """

    @abstractmethod
    def _call_api(self, title: str, details: str) -> str:
        """调用具体的 AI API，返回原始响应文本。

        Args:
            title: 英文产品标题。
            details: 英文产品详情描述。

        Returns:
            API 的原始响应文本。
        """
        ...

    def translate(self, title: str, details: str) -> dict:
        """调用 API 翻译，JSON 解析失败时自动重试一次。

        Args:
            title: 英文产品标题。
            details: 英文产品详情描述。

        Returns:
            包含 core_keywords, russian_title, russian_description 的字典。
        """
        raw = self._call_api(title, details)

        try:
            parsed = _parse_ai_response(raw)
        except ValueError:
            # 重试一次
            raw = self._call_api(title, details)
            parsed = _parse_ai_response(raw)

        parsed["russian_title"] = _post_process_title(parsed["russian_title"])
        return parsed


def _build_user_message(title: str, details: str) -> str:
    """构建发送给 AI 的用户消息。"""
    return (
        f"请将以下英文产品文案翻译并优化为俄语 SEO 文案：\n\n"
        f"标题：{title}\n\n"
        f"详情：{details}"
    )


# ============================================================
# 具体实现
# ============================================================

class OpenAICompatibleProvider(TranslationProvider):
    """OpenAI 兼容协议翻译服务。

    支持 OpenAI、DeepSeek 及其他兼容 API。
    """

    def __init__(self, api_key: str, base_url: str = "", model: str = "gpt-4o") -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    def _call_api(self, title: str, details: str) -> str:
        """调用 OpenAI 兼容 API，返回原始响应文本。"""
        from openai import OpenAI

        kwargs: dict = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        client = OpenAI(**kwargs)

        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _load_system_prompt()},
                {"role": "user", "content": _build_user_message(title, details)},
            ],
            temperature=0.7,
        )

        return response.choices[0].message.content or ""


class ClaudeProvider(TranslationProvider):
    """Anthropic Claude 翻译服务。"""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6") -> None:
        self.api_key = api_key
        self.model = model

    def _call_api(self, title: str, details: str) -> str:
        """调用 Anthropic API，返回原始响应文本。"""
        from anthropic import Anthropic

        client = Anthropic(api_key=self.api_key)

        response = client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=_load_system_prompt(),
            messages=[
                {"role": "user", "content": _build_user_message(title, details)},
            ],
        )

        # Claude 响应可能是 TextBlock 列表
        content = response.content
        if isinstance(content, list):
            return "".join(
                block.text for block in content if hasattr(block, "text")
            )
        return str(content)


# ============================================================
# 工厂函数
# ============================================================

# 使用 OpenAI 兼容协议的 provider 名称集合
_OPENAI_COMPATIBLE_PROVIDERS = {"openai", "deepseek", "custom"}

# 所有支持的 provider 名称
_SUPPORTED_PROVIDERS = _OPENAI_COMPATIBLE_PROVIDERS | {"anthropic"}


def create_translation_provider(
    provider_name: str,
    api_key: str,
    base_url: str = "",
    model: str = "gpt-4o",
) -> TranslationProvider:
    """根据配置创建翻译服务实例。

    Args:
        provider_name: 服务商名称（openai / anthropic / deepseek / custom）。
        api_key: API 密钥。
        base_url: 自定义 API 地址（仅 OpenAI 兼容协议使用）。
        model: 模型名称。

    Returns:
        TranslationProvider 实例。

    Raises:
        ValueError: provider_name 不支持或 api_key 为空时抛出。
    """
    if not api_key or not api_key.strip():
        raise ValueError(
            "API Key 未配置。请在 .env 文件中设置 TRANSLATE_API_KEY，"
            "或设置环境变量 TRANSLATE_API_KEY。"
        )

    name = provider_name.lower().strip()

    if name not in _SUPPORTED_PROVIDERS:
        raise ValueError(
            f"不支持的翻译服务商: '{provider_name}'。"
            f"支持的选项: {', '.join(sorted(_SUPPORTED_PROVIDERS))}"
        )

    if name in _OPENAI_COMPATIBLE_PROVIDERS:
        return OpenAICompatibleProvider(
            api_key=api_key.strip(),
            base_url=base_url.strip(),
            model=model.strip(),
        )

    if name == "anthropic":
        return ClaudeProvider(
            api_key=api_key.strip(),
            model=model.strip(),
        )

    # 理论上不会到达这里，但保持防御性编程
    raise ValueError(f"不支持的翻译服务商: '{provider_name}'")


# ============================================================
# 批量翻译
# ============================================================

# translate_batch 输出的空列占位
_EMPTY_OUTPUT_COLUMNS = {
    "货源": "",
    "采购价": "",
    "商品类别": "",
}


def translate_batch(
    products: list[dict],
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """批量翻译产品列表。

    对每个产品调用 AI 翻译，将结果合并回产品字典。
    输出产品包含全部 12 列所需字段，可直接写入"处理后"Excel。

    Args:
        products: 产品字典列表（来自 read_products_from_excel）。
        progress_callback: 可选回调，签名 (current: int, total: int)。

    Returns:
        翻译后的产品字典列表，包含原字段 + 翻译字段 + 空列。
        如果 API Key 未配置，每个产品含 error 字段而非翻译字段。
    """
    if not products:
        return []

    # 尝试创建 translation provider
    try:
        from config import settings

        provider = create_translation_provider(
            provider_name=settings.translate_api_provider,
            api_key=settings.translate_api_key,
            base_url=settings.translate_api_base_url,
            model=settings.translate_model,
        )
    except ValueError as e:
        # API Key 未配置 → 返回带 error 的结果，不崩溃
        results: list[dict] = []
        for p in products:
            result = dict(p)
            result["error"] = str(e)
            result.update(_EMPTY_OUTPUT_COLUMNS)
            results.append(result)
        return results

    total = len(products)
    results = []
    for i, product in enumerate(products, start=1):
        # 调用翻译
        title = product.get("标题", "")
        details = product.get("详情", "")
        translated = provider.translate(title, details)

        # 合并结果：原始字段 + 翻译字段 + 空列
        result = dict(product)
        result["核心流量词"] = translated.get("core_keywords", "")
        result["俄语标题"] = translated.get("russian_title", "")
        result["俄语详情"] = translated.get("russian_description", "")
        result.update(_EMPTY_OUTPUT_COLUMNS)
        results.append(result)

        # 进度回调
        if progress_callback:
            progress_callback(i, total)

    return results


# ============================================================
# CLI 入口
# ============================================================


def _parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。

    Args:
        argv: 命令行参数列表，None 时使用 sys.argv。

    Returns:
        包含 input 和 output 属性的 Namespace。
    """
    parser = argparse.ArgumentParser(
        description="AI 翻译工具 — 将英文产品文案翻译为俄语 SEO 优化文案",
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="输入 Excel 文件路径（爬虫表格处理前，4列）",
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="输出 Excel 文件路径（翻译处理后，12列）",
    )
    return parser.parse_args(argv)


def main() -> None:
    """CLI 主入口：读取 Excel → 翻译 → 写入 Excel。"""
    args = _parse_cli_args()

    from excel_io import read_products_from_excel, write_translated_products_to_excel

    print(f"读取输入文件: {args.input}")
    products = read_products_from_excel(args.input)
    print(f"共 {len(products)} 个产品待翻译")

    def progress(current: int, total: int) -> None:
        print(f"  翻译进度: {current}/{total}")

    results = translate_batch(products, progress_callback=progress)

    # 检查是否有错误
    errors = [r for r in results if "error" in r]
    if errors:
        print(f"警告: {len(errors)} 个产品翻译失败")
        for e in errors:
            print(f"  - {e['asin']}: {e['error']}")

    print(f"写入输出文件: {args.output}")
    write_translated_products_to_excel(results, args.output)
    print("完成！")


if __name__ == "__main__":
    main()
