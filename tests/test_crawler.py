"""
测试 crawler.py — 爬虫管道编排。

测试行为（非实现）：
- crawl_asins() 编排提取流程，返回产品列表
- 失败自动重试（最多 2 次）
- 进度回调在每个 ASIN 完成后触发
- 验证码检测并抛出明确异常
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(filename: str) -> str:
    path = os.path.join(FIXTURES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ============================================================
# 模拟用 HTML 响应
# ============================================================

VALID_PRODUCT_HTML = _load_fixture("amazon_product_sample.html")

CAPTCHA_HTML = """<html><body>
<form action="/errors/validateCaptcha">
    <img src="/captcha/abc.jpg" />
    <input type="text" name="field-keywords" />
</form>
<p>Enter the characters you see below</p>
</body></html>"""

ERROR_HTML = "<html><body><h1>404 Not Found</h1></body></html>"


# ============================================================
# crawl_asins 测试
# ============================================================

class TestCrawlAsinsPipeline:
    """爬虫管道的基本编排行为。"""

    @pytest.mark.asyncio
    async def test_crawls_single_asin_and_returns_product(self):
        """输入一个 ASIN，返回一个产品字典"""
        from crawler import crawl_asins

        async def fake_fetch(asin):
            return VALID_PRODUCT_HTML

        products = await crawl_asins(
            ["B0GVYXC124"],
            _fetch_func=fake_fetch,
            delay_range=(0, 0),  # 测试时不延迟
        )
        assert len(products) == 1
        p = products[0]
        assert p["asin"] == "B0GVYXC124"
        assert "5-in-1" in p["标题"]
        assert p["图片url"]  # 非空
        assert p["详情"]  # 非空

    @pytest.mark.asyncio
    async def test_crawls_multiple_asins(self):
        """输入多个 ASIN，返回对应数量的产品"""
        from crawler import crawl_asins

        async def fake_fetch(asin):
            return VALID_PRODUCT_HTML

        products = await crawl_asins(
            ["B0GVYXC124", "B0F45N6NS7", "B000000000"],
            _fetch_func=fake_fetch,
            delay_range=(0, 0),
        )
        assert len(products) == 3
        for p in products:
            assert p["asin"] in ("B0GVYXC124", "B0F45N6NS7", "B000000000")

    @pytest.mark.asyncio
    async def test_progress_callback_fires_after_each_asin(self):
        """每完成一个 ASIN，进度回调被调用一次"""
        from crawler import crawl_asins

        progress_calls = []

        def on_progress(current, total, asin, status):
            progress_calls.append((current, total, asin, status))

        async def fake_fetch(asin):
            return VALID_PRODUCT_HTML

        await crawl_asins(
            ["B0A", "B0B", "B0C"],
            progress_callback=on_progress,
            _fetch_func=fake_fetch,
            delay_range=(0, 0),
        )
        assert len(progress_calls) == 3
        assert progress_calls[0] == (1, 3, "B0A", "ok")
        assert progress_calls[1] == (2, 3, "B0B", "ok")
        assert progress_calls[2] == (3, 3, "B0C", "ok")


class TestCrawlAsinsRetry:
    """爬虫失败重试行为。"""

    @pytest.mark.asyncio
    async def test_retries_on_failure_then_succeeds(self):
        """失败后自动重试，第 2 次成功则返回结果"""
        from crawler import crawl_asins

        call_count = 0

        async def flaky_fetch(asin):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("Network error")
            return VALID_PRODUCT_HTML

        products = await crawl_asins(
            ["B0GVYXC124"],
            _fetch_func=flaky_fetch,
            delay_range=(0, 0),
            max_retries=2,
        )
        assert len(products) == 1
        assert call_count == 2  # 第 1 次失败，第 2 次成功
        assert products[0]["asin"] == "B0GVYXC124"

    @pytest.mark.asyncio
    async def test_raises_after_max_retries_exceeded(self):
        """超过最大重试次数仍失败，抛出异常"""
        from crawler import crawl_asins

        async def always_fail(asin):
            raise RuntimeError("Network error")

        with pytest.raises(RuntimeError, match=r"B0GVYXC124.*重试.*2.*次|retried 2 times"):
            await crawl_asins(
                ["B0GVYXC124"],
                _fetch_func=always_fail,
                delay_range=(0, 0),
                max_retries=2,
            )

    @pytest.mark.asyncio
    async def test_progress_shows_failed_on_permanent_error(self):
        """最终失败时进度回调报告 'failed' 状态"""
        from crawler import crawl_asins

        progress_calls = []

        def on_progress(current, total, asin, status):
            progress_calls.append((current, total, asin, status))

        async def always_fail(asin):
            raise RuntimeError("Boom")

        with pytest.raises(RuntimeError):
            await crawl_asins(
                ["B0GVYXC124"],
                progress_callback=on_progress,
                _fetch_func=always_fail,
                delay_range=(0, 0),
                max_retries=2,
            )
        assert len(progress_calls) == 1
        assert progress_calls[0][3] == "failed"


class TestCrawlAsinsCaptcha:
    """验证码检测行为。"""

    @pytest.mark.asyncio
    async def test_detects_captcha_page(self):
        """检测到验证码页面时抛出明确异常"""
        from crawler import crawl_asins

        async def fetch_captcha(asin):
            return CAPTCHA_HTML

        with pytest.raises(RuntimeError, match="[Cc]aptcha|[Vv]erification|[Rr]obot"):
            await crawl_asins(
                ["B0GVYXC124"],
                _fetch_func=fetch_captcha,
                delay_range=(0, 0),
            )

    @pytest.mark.asyncio
    async def test_captcha_not_retried(self):
        """验证码检测到后不应重试（验证码重试无意义）"""
        from crawler import crawl_asins

        call_count = 0

        async def fetch_captcha(asin):
            nonlocal call_count
            call_count += 1
            return CAPTCHA_HTML

        with pytest.raises(RuntimeError, match="[Cc]aptcha|[Vv]erification|[Rr]obot"):
            await crawl_asins(
                ["B0GVYXC124"],
                _fetch_func=fetch_captcha,
                delay_range=(0, 0),
                max_retries=2,
            )
        assert call_count == 1  # 验证码不重试

    @pytest.mark.asyncio
    async def test_captcha_skipped_when_stop_on_first_error_false(self):
        """stop_on_first_error=False 时，验证码不抛异常，跳过该 ASIN 继续"""
        from crawler import crawl_asins

        call_count = 0

        async def mixed_fetch(asin):
            nonlocal call_count
            call_count += 1
            if asin == "B0_CAPTCHA":
                return CAPTCHA_HTML
            return VALID_PRODUCT_HTML

        products = await crawl_asins(
            ["B0_GOOD", "B0_CAPTCHA", "B0_ALSO_GOOD"],
            _fetch_func=mixed_fetch,
            delay_range=(0, 0),
            stop_on_first_error=False,
        )

        # 只有两个成功的产品（B0_GOOD 和 B0_ALSO_GOOD）
        assert len(products) == 2
        succeeded = {p["asin"] for p in products}
        assert "B0_CAPTCHA" not in succeeded
