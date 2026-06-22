"""
图片翻译 UI 功能区 — Streamlit Tab3 业务逻辑层。

提供文件校验、翻译编排、下载生成等纯数据操作，
供 app.py 的 Streamlit UI 层调用。
"""

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ImageUploadResult:
    """图片翻译上传校验结果。

    Attributes:
        is_valid: 校验是否通过。
        error: 失败时的中文错误信息。
        products: 成功时返回的产品列表。
        count: ASIN 数量。
        input_type: "crawler_output"（4列）或 "translation_output"（12列）。
    """
    is_valid: bool = False
    error: str = ""
    products: list[dict] = field(default_factory=list)
    count: int = 0
    input_type: str = ""


def validate_image_upload(filepath: str | Path) -> ImageUploadResult:
    """校验上传的 Excel 文件。

    支持两种格式：
    - 爬虫 4 列输出（asin, 图片url, 标题, 详情）
    - 翻译后 12 列输出（含核心流量词, 俄语标题, 俄语详情 等）

    Args:
        filepath: 上传的 Excel 文件路径。

    Returns:
        ImageUploadResult: 校验结果，含产品列表和格式类型。
    """
    filepath = Path(filepath)
    filename = filepath.name

    if not filename.lower().endswith(".xlsx"):
        return ImageUploadResult(
            is_valid=False,
            error="仅支持 .xlsx 格式的 Excel 文件，请重新上传。",
            count=0,
        )

    import openpyxl

    try:
        wb = openpyxl.load_workbook(filepath, read_only=True)
        ws = wb.active
    except Exception as e:
        return ImageUploadResult(
            is_valid=False,
            error=f"无法打开 Excel 文件: {e}",
            count=0,
        )

    # 读取表头
    headers = [cell.value for cell in ws[1]]
    if headers is None:
        wb.close()
        return ImageUploadResult(
            is_valid=False,
            error="Excel 文件为空或没有表头行。",
            count=0,
        )

    header_set = {str(h).strip() if h is not None else "" for h in headers}

    # 检查必要列
    if "图片url" not in header_set:
        wb.close()
        return ImageUploadResult(
            is_valid=False,
            error="缺少必要列「图片url」。请确保 Excel 包含图片url 列。",
            count=0,
        )

    if "asin" not in header_set:
        wb.close()
        return ImageUploadResult(
            is_valid=False,
            error="缺少必要列「asin」。请确保 Excel 包含 asin 列。",
            count=0,
        )

    # 确定输入类型
    if "俄语标题" in header_set and "核心流量词" in header_set:
        input_type = "translation_output"
    else:
        input_type = "crawler_output"

    # 构建列索引映射
    col_map: dict[str, int] = {}
    for i, h in enumerate(headers):
        key = str(h).strip() if h is not None else ""
        col_map[key] = i

    # 读取数据行
    products: list[dict] = []
    required_cols = {"asin", "图片url"}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if all(v is None or str(v).strip() == "" for v in row):
            continue

        asin = _safe_cell(row, col_map.get("asin"))
        image_urls = _safe_cell(row, col_map.get("图片url"))

        if not asin or not image_urls:
            continue

        product = {"asin": asin, "图片url": image_urls}

        # 读取可选列
        for col_name in ("标题", "详情"):
            if col_name in col_map:
                product[col_name] = _safe_cell(row, col_map[col_name])

        products.append(product)

    wb.close()

    return ImageUploadResult(
        is_valid=True,
        error="",
        products=products,
        count=len(products),
        input_type=input_type,
    )


def _safe_cell(row: tuple, col_idx: int | None) -> str:
    """安全读取单元格值。"""
    if col_idx is None:
        return ""
    if col_idx >= len(row):
        return ""
    val = row[col_idx]
    if val is None:
        return ""
    return str(val).strip()


def start_background_translation(
    products: list[dict],
    font_config=None,
) -> None:
    """启动后台图片翻译 Worker。

    Worker 在独立 asyncio task 中运行，不阻塞 UI。

    Args:
        products: 产品列表。
        font_config: 字体配置。
    """
    from worker import WorkerManager

    manager = WorkerManager()
    manager.start(products=products, font_config=font_config)


def get_worker_status():
    """读取当前 Worker 状态。

    Returns:
        WorkerStatus: 当前进度和状态信息。
    """
    from worker import WorkerManager

    manager = WorkerManager()
    return manager.get_status()


def pause_worker() -> None:
    """暂停后台 Worker。"""
    from worker import WorkerManager

    WorkerManager().pause()


def resume_worker() -> None:
    """恢复后台 Worker。"""
    from worker import WorkerManager

    WorkerManager().resume()


def run_image_translation(
    products: list[dict],
    font_config=None,
):
    """同步运行图片翻译（阻塞直到完成），返回 BatchImageResult。

    用于 Streamlit UI 等需要等待结果的场景。

    Args:
        products: 产品列表。
        font_config: 字体配置。

    Returns:
        BatchImageResult。
    """
    from worker import WorkerManager

    manager = WorkerManager()
    return manager.run_sync(products=products, font_config=font_config)


def generate_output_excel(results) -> bytes:
    """生成翻译后的输出 Excel（图片 URL 已替换为 R2 URL）。

    Args:
        results: BatchImageResult 实例。

    Returns:
        xlsx 文件的二进制数据。
    """
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active

    # 表头（与爬虫输出格式一致 + 新增 R2 URL 列）
    ws.append(["asin", "图片url", "r2图片url", "俄语图片状态"])

    for asin_result in results.results:
        asin = asin_result.asin
        for img in asin_result.images:
            ws.append([
                asin,
                img.original_url,
                img.r2_url,
                img.status,
            ])

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        wb.save(tmp_path)
        wb.close()
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def generate_report(results) -> bytes:
    """生成处理报告（CSV 格式，含每张图的状态和错误信息）。

    Args:
        results: BatchImageResult 实例。

    Returns:
        CSV 文件的二进制数据（UTF-8 BOM）。
    """
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)

    # 表头
    writer.writerow(["asin", "图片序号", "原始URL", "R2 URL", "状态", "错误信息", "原文", "译文"])

    for asin_result in results.results:
        asin = asin_result.asin
        for img in asin_result.images:
            writer.writerow([
                asin,
                img.index,
                img.original_url,
                img.r2_url,
                img.status,
                img.error,
                " | ".join(img.ocr_original_texts) if img.ocr_original_texts else "",
                " | ".join(img.translated_texts) if img.translated_texts else "",
            ])

    # UTF-8 BOM（Excel 兼容）
    return ("﻿" + output.getvalue()).encode("utf-8")
