"""
Web 翻译功能区 — Streamlit Tab2 业务逻辑层。

提供文件校验、翻译编排、下载生成等纯数据操作，
供 app.py 的 Streamlit UI 层调用。

两阶段翻译流水线：
  - Phase 1（AI 信息萃取）：读取 HTML → AI 萃取 → 写入 products 表
  - Phase 2（AI 文案生成）：读 products 表 → AI 生成 → 写入 translations 表
"""

import os
import sqlite3
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
    """翻译编排结果（旧单阶段接口，保留向后兼容）。"""
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


# ============================================================
# 两阶段流水线：Phase 1 — AI 信息萃取
# ============================================================

def execute_phase1_extraction(
    asins: list[str],
    html_dir: str,
    db_path: str,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
    _extractor_override: str | None = None,
) -> list[dict]:
    """执行 Phase 1 信息萃取：读取 HTML → AI 萃取 → 写入 products 表。

    Args:
        asins: ASIN 列表。
        html_dir: HTML 存档目录路径。
        db_path: SQLite 数据库文件路径。
        progress_callback: 可选回调，签名 (current: int, total: int)。
        _extractor_override: 测试注入用，"mock" 使用 MockExtractor。

    Returns:
        结果列表，每项含 asin 和可选的 error 字段。
    """
    from db import init_db

    if not asins:
        return []

    # 初始化数据库
    db = sqlite3.connect(db_path)
    init_db(db)

    # 确定 extractor
    if _extractor_override == "mock":
        from phase1_extractor import MockExtractor
        extractor = MockExtractor()
    else:
        from phase1_extractor import create_extractor
        try:
            extractor = create_extractor()
        except ValueError as e:
            return [{"asin": asin, "error": str(e)} for asin in asins]

    try:
        from phase1_extractor import run_phase1
        results = run_phase1(
            asins=asins,
            html_dir=html_dir,
            db=db,
            extractor=extractor,
            progress_callback=progress_callback,
        )
    finally:
        db.close()

    return results


# ============================================================
# 两阶段流水线：Phase 2 — AI 文案生成
# ============================================================

def execute_phase2_generation(
    asins: list[str],
    db_path: str,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
    _generator_override: str | None = None,
) -> list[dict]:
    """执行 Phase 2 文案生成：读 products 表 → AI 生成 → 写入 translations 表。

    Args:
        asins: ASIN 列表。
        db_path: SQLite 数据库文件路径。
        progress_callback: 可选回调，签名 (current: int, total: int)。
        _generator_override: 测试注入用，"mock" 使用 MockPhase2Generator。

    Returns:
        结果列表，每项含 asin 和可选的 error 字段。
    """
    from db import init_db

    if not asins:
        return []

    # 连接数据库
    db = sqlite3.connect(db_path)
    init_db(db)

    # 确定 generator
    if _generator_override == "mock":
        from phase2_translator import MockPhase2Generator
        generator = MockPhase2Generator()
    else:
        from phase2_translator import create_phase2_generator
        try:
            generator = create_phase2_generator()
        except ValueError as e:
            return [{"asin": asin, "error": str(e)} for asin in asins]

    try:
        from phase2_translator import run_phase2
        results = run_phase2(
            asins=asins,
            db=db,
            generator=generator,
            progress_callback=progress_callback,
        )
    finally:
        db.close()

    return results


# ============================================================
# Phase 2 下载：从数据库生成 12 列 xlsx
# ============================================================

def generate_phase2_download(
    db_path: str,
    asins: list[str],
) -> bytes:
    """从数据库读取 translations + products 生成 12 列 xlsx 下载。

    Args:
        db_path: SQLite 数据库文件路径。
        asins: ASIN 列表（已翻译的）。

    Returns:
        xlsx 文件的二进制数据。
    """
    from db import get_product, get_translation

    db = sqlite3.connect(db_path)

    rows: list[dict] = []
    for asin in asins:
        product = get_product(db, asin)
        translation = get_translation(db, asin)

        row = {
            "asin": asin,
            "图片url": product.get("image_urls", "") if product else "",
            "标题": product.get("title", "") if product else "",
            "详情": product.get("details", "") if product else "",
            "核心流量词": translation.get("core_keywords", "") if translation else "",
            "俄语标题": translation.get("russian_title", "") if translation else "",
            "俄语详情": translation.get("russian_description", "") if translation else "",
            "货源": "",
            "采购价": "",
            "": "",
            "商品类别": "",
        }
        rows.append(row)

    db.close()

    # 写入临时文件再读取为 bytes
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        write_translated_products_to_excel(rows, tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
