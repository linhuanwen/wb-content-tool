"""
Phase 2：AI 文案生成模块。

从 products.db 读取 Phase 1 产出的结构化产品记录，
生成面向 Wildberries 的俄语 SEO 文案，写入 translations 表。

公共接口：
    Phase2Generator       — 抽象基类
    OpenAIPhase2Generator — OpenAI 兼容协议实现
    ClaudePhase2Generator — Anthropic Claude 实现
    MockPhase2Generator   — Mock 实现（不依赖真实 API）
    create_phase2_generator — 工厂函数
    run_phase2            — 批量生成入口
"""

import argparse
import json
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from text_utils import MARKDOWN_JSON_RE, parse_json_response, post_process_title


# ============================================================
# System Prompt 加载
# ============================================================

_PHASE2_PERSONA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "prompts",
    "phase2_translation_persona.txt",
)


def _load_phase2_prompt() -> str:
    """加载 Phase 2 文案生成人设 System Prompt 文件。

    Returns:
        prompts/phase2_translation_persona.txt 的完整内容。

    Raises:
        FileNotFoundError: 文件不存在时抛出。
    """
    path = Path(_PHASE2_PERSONA_PATH)
    if not path.is_file():
        raise FileNotFoundError(
            f"Phase 2 文案生成人设文件不存在: {_PHASE2_PERSONA_PATH}\n"
            f"请确保 prompts/phase2_translation_persona.txt 文件存在。"
        )
    return path.read_text(encoding="utf-8")


# ============================================================
# 构建 User Message
# ============================================================

def _build_user_message_from_record(product_record: dict) -> str:
    """从结构化产品记录构建发送给 AI 的用户消息。

    将产品记录的各个字段格式化为 JSON，提供给 AI 作为输入。

    Args:
        product_record: Phase 1 产出的结构化产品字典（来自 db.get_product）。

    Returns:
        用户消息字符串。
    """
    # 构建结构化输入 JSON（只包含 AI 需要的字段）
    input_data = {
        "asin": product_record.get("asin", ""),
        "category": product_record.get("category", ""),
        "title": product_record.get("title", ""),
        "details": product_record.get("details", ""),
        "material": product_record.get("material", ""),
        "color": product_record.get("color", ""),
        "dimensions": product_record.get("dimensions", ""),
        "weight": product_record.get("weight", ""),
        "capacity": product_record.get("capacity", ""),
        "package_contents": product_record.get("package_contents", ""),
        "features": product_record.get("features", []),
        "technical_specs": product_record.get("technical_specs", {}),
        "target_audience": product_record.get("target_audience", ""),
        "use_scenarios": product_record.get("use_scenarios", []),
        "unique_selling_points": product_record.get("unique_selling_points", []),
        "brand": product_record.get("brand", ""),
        "en_search_keywords": product_record.get("en_search_keywords", []),
    }

    input_json = json.dumps(input_data, ensure_ascii=False, indent=2)

    return (
        f"请基于以下结构化产品数据，生成面向 Wildberries 的俄语 SEO 优化文案。\n\n"
        f"关键提醒：\n"
        f"1. 根据 category 字段采用品类差异化文案策略\n"
        f"2. 根据 brand 字段精确定位并去除品牌词（不误删普通词）\n"
        f"3. 标题 ≤60字符、全小写、无特殊符号\n"
        f"4. 详情中核心关键词以同义词形式自然重复 2-3 次\n"
        f"5. 利用 en_search_keywords 作为关键词灵感\n\n"
        f"=== 结构化产品数据 ===\n{input_json}\n\n"
        f"请严格按照 System Prompt 中定义的 JSON 格式输出，不要输出任何其他内容。"
    )


# ============================================================
# JSON 解析
# ============================================================

# Phase 2 必填字段
_PHASE2_REQUIRED_FIELDS = {"russian_title", "core_keywords", "russian_description"}


def _parse_phase2_response(text: str) -> dict:
    """从 AI 响应文本中解析 Phase 2 文案 JSON。

    委托给 text_utils.parse_json_response。
    """
    return parse_json_response(text, required_fields=_PHASE2_REQUIRED_FIELDS)


# ============================================================
# 抽象接口
# ============================================================

class Phase2Generator(ABC):
    """AI 文案生成抽象基类。

    子类只需实现 _call_api 方法。generate 方法提供统一的
    解析、重试、后处理逻辑。
    """

    @abstractmethod
    def _call_api(self, product_record: dict) -> str:
        """调用具体的 AI API，返回原始响应文本。

        Args:
            product_record: 结构化产品字典（来自 db.get_product）。

        Returns:
            API 的原始响应文本。
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """返回当前使用的模型名称。"""
        ...

    def generate(self, product_record: dict) -> dict:
        """调用 API 生成文案，JSON 解析失败时自动重试一次。

        Args:
            product_record: 结构化产品字典。

        Returns:
            包含 core_keywords, russian_title, russian_description 的字典。
        """
        raw = self._call_api(product_record)

        try:
            parsed = _parse_phase2_response(raw)
        except ValueError:
            # 重试一次
            raw = self._call_api(product_record)
            parsed = _parse_phase2_response(raw)

        parsed["russian_title"] = post_process_title(parsed["russian_title"])
        return parsed


# ============================================================
# 具体实现
# ============================================================

class OpenAIPhase2Generator(Phase2Generator):
    """OpenAI 兼容协议文案生成服务。

    支持 OpenAI、DeepSeek 及其他兼容 API。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "",
        model: str = "gpt-4o",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    @property
    def model_name(self) -> str:
        return self.model

    def _call_api(self, product_record: dict) -> str:
        """调用 OpenAI 兼容 API，返回原始响应文本。"""
        from openai import OpenAI

        kwargs: dict = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        client = OpenAI(**kwargs)

        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _load_phase2_prompt()},
                {"role": "user", "content": _build_user_message_from_record(product_record)},
            ],
            temperature=0.3,
        )

        return response.choices[0].message.content or ""


class ClaudePhase2Generator(Phase2Generator):
    """Anthropic Claude 文案生成服务。"""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self.api_key = api_key
        self.model = model

    @property
    def model_name(self) -> str:
        return self.model

    def _call_api(self, product_record: dict) -> str:
        """调用 Anthropic API，返回原始响应文本。"""
        from anthropic import Anthropic

        client = Anthropic(api_key=self.api_key)

        response = client.messages.create(
            model=self.model,
            max_tokens=2048,
            temperature=0.3,
            system=_load_phase2_prompt(),
            messages=[
                {"role": "user", "content": _build_user_message_from_record(product_record)},
            ],
        )

        # Claude 响应可能是 TextBlock 列表
        content = response.content
        if isinstance(content, list):
            return "".join(
                block.text for block in content if hasattr(block, "text")
            )
        return str(content)


class MockPhase2Generator(Phase2Generator):
    """Mock 文案生成器，返回固定测试数据，不依赖真实 API。

    用于单元测试和开发调试。
    """

    @property
    def model_name(self) -> str:
        return "mock"

    def _call_api(self, product_record: dict) -> str:
        """Mock 实现不调用 API，直接返回预设 JSON。"""
        # 根据产品记录生成确定性但略有区别的输出
        asin = product_record.get("asin", "unknown")
        category = product_record.get("category", "")

        if "Kitchen" in category or "Home" in category:
            return json.dumps({
                "russian_title": "бутылка для масла стеклянная с распылителем",
                "core_keywords": "бутылка для масла стеклянная банка для масла кухонный инвентарь емкость для масла",
                "russian_description": "Стеклянная бутылка для масла с удобным распылителем станет незаменимым помощником на вашей кухне. Изготовлена из прочного стекла, устойчивого к царапинам и сколам. Идеально подходит для хранения оливкового, подсолнечного и других растительных масел.",
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "russian_title": "массажер для лица 5 в 1 с микротоками и led",
                "core_keywords": "микротоковый массажер для лица аппарат для подтяжки лица ультразвуковой массажер рф лифтинг led маска для лица антивозрастной уход домашний косметологический аппарат",
                "russian_description": "Многофункциональный массажер для лица объединяет 5 передовых технологий омоложения в одном компактном устройстве. Микротоки мягко подтягивают контур лица, ультразвук улучшает проникновение сывороток, а радиочастотный лифтинг разглаживает мелкие морщины. Идеально для домашнего ухода за лицом и антивозрастных процедур без посещения косметолога.",
            }, ensure_ascii=False)


# ============================================================
# 工厂函数
# ============================================================

# 使用 OpenAI 兼容协议的 provider 名称集合
_OPENAI_COMPATIBLE_PROVIDERS = {"openai", "deepseek", "custom"}

# 所有支持的 provider 名称
_SUPPORTED_PROVIDERS = _OPENAI_COMPATIBLE_PROVIDERS | {"anthropic", "mock"}


def create_phase2_generator(
    provider_name: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> Phase2Generator:
    """根据配置创建文案生成服务实例。

    参数未提供时从 config.settings 读取 Phase 2 配置。

    Args:
        provider_name: 服务商名称。None 时从配置读取。
        api_key: API 密钥。None 时从配置读取。
        base_url: 自定义 API 地址。None 时从配置读取。
        model: 模型名称。None 时从配置读取。

    Returns:
        Phase2Generator 实例。

    Raises:
        ValueError: provider_name 不支持或 api_key 为空时抛出。
    """
    from config import settings

    name = (provider_name if provider_name is not None else settings.phase2_api_provider).lower().strip()
    key = (api_key if api_key is not None else settings.phase2_api_key).strip()
    url = (base_url if base_url is not None else settings.phase2_api_base_url).strip()
    mdl = (model if model is not None else settings.phase2_model).strip()

    # Mock 不需要 API Key
    if name == "mock":
        return MockPhase2Generator()

    if not key:
        raise ValueError(
            "Phase 2 API Key 未配置。请在 .env 文件中设置 PHASE2_API_KEY，"
            "或设置环境变量 PHASE2_API_KEY。"
        )

    if name not in _SUPPORTED_PROVIDERS:
        raise ValueError(
            f"不支持的 Phase 2 服务商: '{name}'。"
            f"支持的选项: {', '.join(sorted(_SUPPORTED_PROVIDERS))}"
        )

    if name in _OPENAI_COMPATIBLE_PROVIDERS:
        return OpenAIPhase2Generator(
            api_key=key,
            base_url=url,
            model=mdl,
        )

    if name == "anthropic":
        return ClaudePhase2Generator(
            api_key=key,
            model=mdl,
        )

    raise ValueError(f"不支持的 Phase 2 服务商: '{name}'")


# ============================================================
# 批量生成
# ============================================================

def run_phase2(
    asins: list[str],
    db: "sqlite3.Connection",
    generator: Phase2Generator | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """批量生成俄语文案并写入数据库。

    遍历 ASIN → 从 products 表读取 → 调 AI 生成 → 写入 translations 表。

    Args:
        asins: ASIN 列表。
        db: sqlite3 连接对象（需已调用 init_db）。
        generator: Phase2Generator 实例。None 时通过 create_phase2_generator() 创建。
        progress_callback: 可选回调，签名 (current: int, total: int)。

    Returns:
        结果列表，每项含 asin 和可选的 error 字段。
    """
    import sqlite3

    from db import get_product, upsert_translation

    if not asins:
        return []

    # 创建 generator（如果未提供）
    if generator is None:
        try:
            generator = create_phase2_generator()
        except ValueError as e:
            # API Key 未配置 → 全部返回 error，不崩溃
            return [{"asin": asin, "error": str(e)} for asin in asins]

    total = len(asins)
    results: list[dict] = []

    for i, asin in enumerate(asins, start=1):
        # 读取产品记录
        product = get_product(db, asin)
        if product is None:
            results.append({"asin": asin, "error": f"产品记录不存在: {asin}"})
            if progress_callback:
                progress_callback(i, total)
            continue

        # AI 生成
        try:
            translation = generator.generate(product)
        except Exception as e:
            results.append({"asin": asin, "error": f"AI 文案生成失败: {e}"})
            if progress_callback:
                progress_callback(i, total)
            continue

        # 写入数据库
        try:
            upsert_translation(db, {
                "asin": asin,
                "russian_title": translation.get("russian_title", ""),
                "core_keywords": translation.get("core_keywords", ""),
                "russian_description": translation.get("russian_description", ""),
                "phase1_model": "",  # Phase 1 的模型由 Phase 1 记录
                "phase2_model": generator.model_name,
            })
            results.append({"asin": asin})
        except Exception as e:
            results.append({"asin": asin, "error": f"写入数据库失败: {e}"})

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
        包含 db 和 asin 属性的 Namespace。
    """
    parser = argparse.ArgumentParser(
        description="Phase 2: AI 文案生成 — 基于结构化产品记录生成 Wildberries 俄语 SEO 文案",
    )
    parser.add_argument(
        "--db",
        required=True,
        help="SQLite 数据库文件路径（如 products.db）",
    )
    parser.add_argument(
        "--asin",
        default=None,
        help="可选：只处理指定 ASIN。不传则处理全部待翻译产品。",
    )
    return parser.parse_args(argv)


def main() -> None:
    """CLI 主入口：查询待翻译产品 → AI 生成文案 → 写入 translations 表。"""
    import sqlite3

    from db import init_db, get_products_pending_phase2

    args = _parse_cli_args()

    db_path = args.db

    # 连接数据库
    db = sqlite3.connect(db_path)
    init_db(db)

    # 确定要处理的 ASIN 列表
    if args.asin:
        asins = [args.asin]
        print(f"单条生成模式: ASIN = {args.asin}")
    else:
        asins = get_products_pending_phase2(db)
        print(f"批量生成模式: 发现 {len(asins)} 个待翻译产品")

    if not asins:
        print("没有需要处理的产品。")
        db.close()
        return

    # 创建 generator
    try:
        generator = create_phase2_generator()
    except ValueError as e:
        print(f"错误: {e}")
        db.close()
        return

    def progress(current: int, total: int) -> None:
        print(f"  生成进度: {current}/{total}")

    print(f"开始 AI 文案生成...")
    results = run_phase2(
        asins=asins,
        db=db,
        generator=generator,
        progress_callback=progress,
    )

    # 报告结果
    success = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]

    print(f"\n完成！成功: {len(success)}, 失败: {len(errors)}")
    for e in errors:
        print(f"  - {e['asin']}: {e['error']}")

    db.close()


if __name__ == "__main__":
    main()
