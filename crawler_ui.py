"""
Web 爬虫功能区 — Streamlit Tab1 业务逻辑层。

提供文件校验、采集编排、下载生成等纯数据操作，
供 app.py 的 Streamlit UI 层调用。
"""

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from config import settings
from crawler import crawl_asins
from excel_io import read_asins_from_excel, write_products_to_excel


@dataclass
class UploadResult:
    """文件上传校验结果。"""
    is_valid: bool
    error: str           # 失败时的中文错误信息
    asins: list[str]     # 成功时返回 ASIN 列表
    count: int           # ASIN 数量


@dataclass
class CrawlResult:
    """采集编排结果。"""
    products: list[dict] = field(default_factory=list)        # 成功采集的产品
    failed_asins: list[str] = field(default_factory=list)     # 采集失败的 ASIN


def validate_upload(filepath: str | Path) -> UploadResult:
    """校验上传文件。

    检查文件扩展名是否为 .xlsx，然后检测是否包含 asin 列。

    Args:
        filepath: 上传的 Excel 文件路径。

    Returns:
        UploadResult: 校验结果。
    """
    filepath = Path(filepath)
    filename = filepath.name

    if not filename.lower().endswith(".xlsx"):
        return UploadResult(
            is_valid=False,
            error="仅支持 .xlsx 格式的 Excel 文件，请重新上传。",
            asins=[],
            count=0,
        )

    # 检测 ASIN 列
    try:
        asins = read_asins_from_excel(filepath)
    except ValueError as e:
        return UploadResult(
            is_valid=False,
            error=str(e),
            asins=[],
            count=0,
        )

    return UploadResult(is_valid=True, error="", asins=asins, count=len(asins))


async def execute_crawl(
    asins: list[str],
    *,
    headless: bool = True,
    delay_min: float = 3.0,
    delay_max: float = 8.0,
    max_retries: int = 2,
    progress_callback: Callable[[int, int, str, str], None] | None = None,
    _fetch_func: Callable | None = None,
) -> CrawlResult:
    """编排爬虫采集。

    调用 crawler.crawl_asins()，以"不中断"模式运行：单个 ASIN
    失败不抛异常，继续采集其余 ASIN。

    Args:
        asins: 待采集的 ASIN 列表。
        headless: 是否无头模式运行浏览器。
        delay_min: 最小请求间隔（秒）。
        delay_max: 最大请求间隔（秒）。
        max_retries: 每个 ASIN 的最大重试次数。
        progress_callback: 进度回调，签名 (current, total, asin, status)。
        _fetch_func: 页面获取函数（测试注入用）。

    Returns:
        CrawlResult: 成功产品列表 + 失败 ASIN 列表。
    """
    # 应用 UI 侧边栏的无头模式配置
    if not headless:
        settings.crawler_headless = False
    else:
        settings.crawler_headless = True

    products = await crawl_asins(
        asins,
        progress_callback=progress_callback,
        _fetch_func=_fetch_func,
        delay_range=(delay_min, delay_max),
        max_retries=max_retries,
        stop_on_first_error=False,
    )

    # 推断失败的 ASIN
    succeeded_asins = {p["asin"] for p in products}
    failed = [a for a in asins if a not in succeeded_asins]

    return CrawlResult(products=products, failed_asins=failed)


def generate_download(products: list[dict]) -> bytes:
    """将产品列表转为"爬虫表格（处理前）"格式的 xlsx 字节流。

    Args:
        products: 产品字典列表。

    Returns:
        xlsx 文件的二进制数据，可直接用于 Streamlit download_button。
    """
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        write_products_to_excel(products, tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
