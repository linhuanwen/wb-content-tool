"""
Web 翻译功能区 — Streamlit Tab2 业务逻辑层。

提供文件校验、翻译编排、下载生成等纯数据操作，
供 app.py 的 Streamlit UI 层调用。
"""

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from excel_io import write_translated_products_to_excel


@dataclass
class TranslationUploadResult:
    """文件上传校验结果。"""
    is_valid: bool
    error: str                # 失败时的中文错误信息
    products: list[dict]      # 成功时返回待翻译产品列表
    count: int                # 产品数量


@dataclass
class TranslationResult:
    """翻译编排结果。"""
    translated: list[dict] = field(default_factory=list)   # 翻译成功的产品
    failed: list[dict] = field(default_factory=list)       # 翻译失败的产品（含 error）


def validate_translation_upload(filepath: str | Path) -> TranslationUploadResult:
    """校验上传文件。

    检查文件扩展名是否为 .xlsx。

    Args:
        filepath: 上传的 Excel 文件路径。

    Returns:
        TranslationUploadResult: 校验结果。
    """
    filepath = Path(filepath)
    filename = filepath.name

    if not filename.lower().endswith(".xlsx"):
        return TranslationUploadResult(
            is_valid=False,
            error="仅支持 .xlsx 格式的 Excel 文件，请重新上传。",
            products=[],
            count=0,
        )

    # 校验必要列并读取产品
    try:
        from excel_io import read_products_from_excel

        products = read_products_from_excel(filepath)
    except ValueError as e:
        return TranslationUploadResult(
            is_valid=False,
            error=str(e),
            products=[],
            count=0,
        )

    return TranslationUploadResult(
        is_valid=True, error="", products=products, count=len(products)
    )


def execute_translation(
    products: list[dict],
    *,
    progress_callback: Callable[[int, int], None] | None = None,
    _provider_override=None,
) -> TranslationResult:
    """执行翻译，分离成功/失败。

    Args:
        products: 待翻译产品列表（来自 validate_translation_upload）。
        progress_callback: 可选回调，签名 (current: int, total: int)。
        _provider_override: 测试注入用，直接使用给定的 TranslationProvider。

    Returns:
        TranslationResult: 翻译成功 + 失败产品分离结果。
    """
    if not products:
        return TranslationResult(translated=[], failed=[])

    # 确定使用的 provider（测试注入优先，否则从配置创建）
    if _provider_override is not None:
        provider = _provider_override
    else:
        from translator import create_translation_provider
        from config import settings

        try:
            provider = create_translation_provider(
                provider_name=settings.translate_api_provider,
                api_key=settings.translate_api_key,
                base_url=settings.translate_api_base_url,
                model=settings.translate_model,
            )
        except ValueError as e:
            # API Key 未配置 → 全部入 failed
            failed = []
            for product in products:
                failed_item = dict(product)
                failed_item["error"] = str(e)
                failed.append(failed_item)
            return TranslationResult(translated=[], failed=failed)

    translated: list[dict] = []
    failed: list[dict] = []
    total = len(products)

    for i, product in enumerate(products, start=1):
        title = product.get("标题", "")
        details = product.get("详情", "")

        try:
            result_fields = provider.translate(title, details)

            # 合并结果：原始字段 + 翻译字段 + 空列
            result = dict(product)
            result["核心流量词"] = result_fields.get("core_keywords", "")
            result["俄语标题"] = result_fields.get("russian_title", "")
            result["俄语详情"] = result_fields.get("russian_description", "")
            result["货源"] = ""
            result["采购价"] = ""
            result["商品类别"] = ""
            translated.append(result)

        except Exception as e:
            failed_item = dict(product)
            failed_item["error"] = str(e)
            failed.append(failed_item)

        if progress_callback:
            progress_callback(i, total)

    return TranslationResult(translated=translated, failed=failed)


def generate_translation_download(translated_products: list[dict]) -> bytes:
    """将翻译后的产品列表转为"爬虫表格（处理后）"格式的 xlsx 字节流。

    Args:
        translated_products: 翻译成功的产品字典列表（含全部 12 列字段）。

    Returns:
        xlsx 文件的二进制数据，可直接用于 Streamlit download_button。
    """
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        write_translated_products_to_excel(translated_products, tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
