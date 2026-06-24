"""
亚马逊商品爬虫模块。

提供：
- crawl_asins(): 异步爬虫管道，供 Web 界面调用
- CLI: python crawler.py --input xxx.xlsx --output yyy.xlsx
"""

import argparse
import asyncio
import logging
import os
import random
from pathlib import Path
from typing import Callable

import httpx

from config import settings
from excel_io import read_asins_from_excel, write_products_to_excel

logger = logging.getLogger(__name__)


class CaptchaError(RuntimeError):
    """亚马逊验证码检测异常——不应重试，需手动处理。"""


# 常见的 User-Agent 列表，用于反爬
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


def _detect_captcha(html: str) -> bool:
    """检测 HTML 是否包含亚马逊验证码页面。

    Args:
        html: 页面 HTML 源码。

    Returns:
        如果检测到验证码页面返回 True。
    """
    html_lower = html.lower()
    captcha_indicators = [
        "validatecaptcha",
        "enter the characters you see below",
        "type the characters you see",
        "sorry, we just need to make sure you're not a robot",
        "robot check",
        "captcha",
    ]
    return any(indicator in html_lower for indicator in captcha_indicators)


async def _fetch_page_playwright(asin: str) -> str:
    """使用 Playwright 获取亚马逊产品页 HTML（生产环境实现）。

    Args:
        asin: 产品 ASIN。

    Returns:
        页面 HTML 源码。

    Raises:
        ImportError: 如果 Playwright 未安装。
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ImportError(
            "Playwright 未安装。请运行: pip install playwright && playwright install chromium"
        )

    url = f"https://www.amazon.com/dp/{asin}"
    user_agent = random.choice(USER_AGENTS)

    async with async_playwright() as p:
        # 优先使用系统已安装的 Chrome，无需额外下载 182MB Chromium
        try:
            browser = await p.chromium.launch(
                channel="chrome", headless=settings.crawler_headless
            )
        except Exception:
            browser = await p.chromium.launch(headless=settings.crawler_headless)
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # 模拟滚动（反爬）
            await page.evaluate("window.scrollBy(0, 300)")
            await asyncio.sleep(0.5)
            await page.evaluate("window.scrollBy(0, 600)")
            await asyncio.sleep(0.5)

            html = await page.content()
            return html
        finally:
            await browser.close()


async def _fetch_page_scraperapi(asin: str) -> str:
    """使用 ScraperAPI 获取亚马逊产品页 HTML。

    通过 ScraperAPI 的代理服务访问亚马逊，自动处理 IP 轮换、
    CAPTCHA 和 JS 渲染。

    Args:
        asin: 产品 ASIN。

    Returns:
        页面 HTML 源码。

    Raises:
        ValueError: 如果 ScraperAPI Key 未配置。
        RuntimeError: 如果 ScraperAPI 返回错误。
    """
    api_key = settings.scraperapi_key
    if not api_key:
        raise ValueError(
            "ScraperAPI Key 未配置。请在 .env 文件中设置 SCRAPERAPI_KEY。"
        )

    url = f"https://www.amazon.com/dp/{asin}"
    params = {
        "api_key": api_key,
        "url": url,
        "country_code": "us",
        "device_type": "desktop",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(3):  # 最多重试 3 次
            try:
                resp = await client.get(
                    "https://api.scraperapi.com/", params=params
                )
                if resp.status_code == 200:
                    html = resp.text
                    # 检查是否是 ScraperAPI 错误响应
                    if "scraperapi" in html.lower()[:200]:
                        raise RuntimeError(
                            f"ScraperAPI 返回异常响应: {html[:300]}"
                        )
                    return html
                elif resp.status_code == 403:
                    raise RuntimeError(
                        "ScraperAPI 返回 403: API Key 可能无效或配额已用完。"
                    )
                elif resp.status_code == 500:
                    if attempt < 2:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    raise RuntimeError(
                        f"ScraperAPI 返回 500 错误（已重试 3 次），"
                        f"可能是亚马逊暂时拒绝访问。"
                    )
                else:
                    raise RuntimeError(
                        f"ScraperAPI 返回 HTTP {resp.status_code}: "
                        f"{resp.text[:200]}"
                    )
            except httpx.TimeoutException:
                if attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise RuntimeError(
                    "ScraperAPI 请求超时（已重试 3 次）。"
                )


def _get_default_fetcher():
    """根据配置返回默认的页面获取函数。"""
    if settings.crawler_mode == "scraperapi":
        return _fetch_page_scraperapi
    return _fetch_page_playwright


async def crawl_asins(
    asins: list[str],
    progress_callback: Callable[[int, int, str, str], None] | None = None,
    *,
    _fetch_func: Callable[[str], str] | None = None,
    delay_range: tuple[float, float] = (3.0, 8.0),
    max_retries: int = 2,
    stop_on_first_error: bool = True,
) -> list[dict]:
    """批量爬取亚马逊产品页 HTML 并存档。

    对每个 ASIN 依次执行：获取页面 → 检测验证码 → 存档 HTML。
    失败时自动重试（最多 max_retries 次），验证码不重试。

    HTML 存档到 html/{ASIN}.html，供 Phase 1 AI 萃取使用。
    标题/图片url/详情由 Phase 1 从 HTML 中提取并写入数据库，爬虫不再负责提取。

    Args:
        asins: ASIN 列表。
        progress_callback: 进度回调，签名 (current, total, asin, status)。
            status 取值: "ok" | "failed"。
        _fetch_func: 页面获取函数（测试注入用，默认使用 Playwright）。
        delay_range: 请求间隔范围（秒），在范围内随机延迟。
        max_retries: 每个 ASIN 的最大重试次数（不含首次尝试）。
        stop_on_first_error: 默认 True，单个 ASIN 失败时抛异常。
            设为 False 时继续采集其余 ASIN，只返回成功的结果。

    Returns:
        产品信息字典列表，每个字典包含 asin, 标题, 图片url, 详情。
        标题/图片url/详情当前返回空字符串（由 Phase 1 填充）。
        当 stop_on_first_error=False 时，失败的 ASIN 不在返回列表中。

    Raises:
        RuntimeError: stop_on_first_error=True 时，ASIN 爬取失败且超过最大重试次数时抛出。
    """
    products: list[dict] = []
    total = len(asins)
    fetch = _fetch_func or _get_default_fetcher()

    for i, asin in enumerate(asins, start=1):
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                html = await fetch(asin)

                if _detect_captcha(html):
                    raise CaptchaError(
                        f"ASIN {asin}: 检测到亚马逊验证码（Captcha）页面。"
                        f"请手动在浏览器中完成验证后重试，或等待一段时间后再爬取。"
                    )

                # 存档 HTML 到 html/{ASIN}.html
                html_dir = Path("html")
                html_dir.mkdir(parents=True, exist_ok=True)
                html_path = html_dir / f"{asin}.html"
                html_path.write_text(html, encoding="utf-8")

                # 从 HTML 中确定性提取标题/图片url/详情
                # （Phase 1 AI 萃取负责更丰富的 15 个语义字段）
                from phase1_extractor import _extract_basic_fields
                basic = _extract_basic_fields(html)
                info = {
                    "asin": asin,
                    "标题": basic["title"],
                    "图片url": basic["image_urls"],
                    "详情": basic["details"],
                }
                products.append(info)

                if progress_callback:
                    progress_callback(i, total, asin, "ok")
                break  # 成功，跳出重试循环

            except CaptchaError:
                if stop_on_first_error:
                    raise  # 验证码不重试，直接向上抛
                else:
                    logger.warning(f"ASIN {asin}: 检测到验证码，跳过。")
                    if progress_callback:
                        progress_callback(i, total, asin, "failed")
                    break  # 跳出重试循环，继续下一个 ASIN

            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(
                        f"ASIN {asin} 第 {attempt + 1} 次尝试失败: {e}，准备重试..."
                    )
                    await asyncio.sleep(1)  # 重试前短暂等待
                else:
                    # 所有重试耗尽
                    if progress_callback:
                        progress_callback(i, total, asin, "failed")
                    if stop_on_first_error:
                        raise RuntimeError(
                            f"ASIN {asin}: 爬取失败（已重试 {max_retries} 次）。"
                            f"最后错误: {last_error}"
                        ) from last_error
                    else:
                        logger.warning(
                            f"ASIN {asin}: 爬取失败（已重试 {max_retries} 次），"
                            f"跳过继续。最后错误: {last_error}"
                        )

        # 请求间隔（使用设定范围的随机值）
        if i < total:
            delay = random.uniform(*delay_range)
            await asyncio.sleep(delay)

    return products


# ============================================================
# CLI
# ============================================================

def main():
    """CLI 入口: python crawler.py --input asin列表.xlsx --output 处理前.xlsx"""
    parser = argparse.ArgumentParser(
        description="亚马逊商品爬虫 — 输入 ASIN 列表 Excel，输出产品信息 Excel"
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="输入 Excel 文件路径（包含 asin 列）",
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="输出 Excel 文件路径",
    )
    parser.add_argument(
        "--delay-min",
        type=float,
        default=settings.crawler_delay - 2 if settings.crawler_delay > 2 else 1,
        help=f"最小请求间隔秒数（默认: {settings.crawler_delay - 2 if settings.crawler_delay > 2 else 1}）",
    )
    parser.add_argument(
        "--delay-max",
        type=float,
        default=settings.crawler_delay + 3,
        help=f"最大请求间隔秒数（默认: {settings.crawler_delay + 3}）",
    )

    args = parser.parse_args()

    # 读取 ASIN 列表
    print(f"读取 ASIN 列表: {args.input}")
    asins = read_asins_from_excel(args.input)
    print(f"共 {len(asins)} 个 ASIN")

    # 运行爬虫
    def progress(current, total, asin, status):
        emoji = "✅" if status == "ok" else "❌"
        print(f"  [{current}/{total}] {asin} {emoji}")

    products = asyncio.run(
        crawl_asins(
            asins,
            progress_callback=progress,
            delay_range=(args.delay_min, args.delay_max),
        )
    )

    # 写入输出
    write_products_to_excel(products, args.output)
    print(f"输出文件: {args.output} ({len(products)} 条产品记录)")


if __name__ == "__main__":
    main()
