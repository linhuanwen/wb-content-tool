"""
测试 extractor.py — 从亚马逊 HTML 提取产品信息。

测试行为（非实现）：
- 从产品页面 HTML 提取标题、图片 URL（;分隔）、详情
- 处理缺失字段时优雅降级
- 图片 URL 去重和排序
- 移除亚马逊图片 URL 尺寸后缀以获取高分辨率图片
- 优先使用 data-old-hires 属性
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(filename: str) -> str:
    """加载测试夹具 HTML 文件。"""
    path = os.path.join(FIXTURES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class TestExtractTitle:
    """提取产品标题。"""

    def test_extracts_title_from_product_title(self):
        """从 #productTitle 提取标题文本（去除首尾空白）"""
        from extractor import extract_product_info

        html = _load_fixture("amazon_product_sample.html")
        info = extract_product_info(html, "B0GVYXC124")
        assert "5-in-1 Face & Body Sculpting Machine" in info["标题"]
        assert len(info["标题"]) > 10

    def test_title_is_stripped(self):
        """标题去除首尾空白和换行"""
        html = '<html><body><div id="productTitle">\n  Test Product Title  \n</div></body></html>'
        from extractor import extract_product_info

        info = extract_product_info(html, "B000000000")
        assert info["标题"] == "Test Product Title"


class TestExtractImages:
    """提取产品图片 URL。"""

    def test_extracts_multiple_image_urls(self):
        """从 #altImages 提取多张图片 URL，用分号分隔"""
        from extractor import extract_product_info

        html = _load_fixture("amazon_product_sample.html")
        info = extract_product_info(html, "B0GVYXC124")
        urls = info["图片url"].split(";")

        assert len(urls) >= 2, f"期望 ≥2 张图片，实际: {len(urls)}"
        assert all(u.startswith("http") for u in urls), f"URL 格式不正确: {urls}"

    def test_uses_semicolon_separator(self):
        """多图 URL 必须以分号（;）分隔，符合 Wildberries 要求"""
        from extractor import extract_product_info

        html = _load_fixture("amazon_product_sample.html")
        info = extract_product_info(html, "B0GVYXC124")
        assert ";" in info["图片url"]
        assert "," not in info["图片url"]

    def test_deduplicates_image_urls(self):
        """相同图片 URL 不去重保留一个（亚马逊可能有同一图片不同分辨率）"""
        html = """
        <html><body>
        <div id="altImages">
            <img src="https://img1.jpg" />
            <img src="https://img1.jpg" />
            <img src="https://img2.jpg" />
        </div>
        </body></html>
        """
        from extractor import extract_product_info

        info = extract_product_info(html, "B000000000")
        urls = info["图片url"].split(";")
        # 重复 URL 去重
        assert len(urls) == 2, f"应去重，实际: {len(urls)}"
        assert "img1.jpg" in info["图片url"]
        assert "img2.jpg" in info["图片url"]


class TestNormalizeImageUrl:
    """移除亚马逊图片 URL 尺寸后缀以获取原始分辨率。"""

    def test_strips_ac_us40_suffix(self):
        """去掉 ._AC_US40_ 后缀（40px 缩略图）"""
        from extractor import _normalize_image_url

        url = "https://m.media-amazon.com/images/I/71KpYEV4hPL._AC_US40_.jpg"
        result = _normalize_image_url(url)
        assert result == "https://m.media-amazon.com/images/I/71KpYEV4hPL.jpg"

    def test_strips_sx_sy_suffix(self):
        """去掉 ._SX38_SY50_ 后缀"""
        from extractor import _normalize_image_url

        url = "https://m.media-amazon.com/images/I/71abcDEfgh2._SX38_SY50_.jpg"
        result = _normalize_image_url(url)
        assert result == "https://m.media-amazon.com/images/I/71abcDEfgh2.jpg"

    def test_strips_ac_sl_suffix(self):
        """去掉 ._AC_SL1500_ 后缀"""
        from extractor import _normalize_image_url

        url = "https://m.media-amazon.com/images/I/71xyzLMNop3._AC_SL1500_.jpg"
        result = _normalize_image_url(url)
        assert result == "https://m.media-amazon.com/images/I/71xyzLMNop3.jpg"

    def test_clean_url_unchanged(self):
        """不含尺寸后缀的 URL 原样返回"""
        from extractor import _normalize_image_url

        url = "https://m.media-amazon.com/images/I/71KpYEV4hPL.jpg"
        result = _normalize_image_url(url)
        assert result == url

    def test_url_with_multiple_suffixes(self):
        """URL 含有多个尺寸后缀时全部移除"""
        from extractor import _normalize_image_url

        url = "https://m.media-amazon.com/images/I/41abcde._SX425_CR,0,0,425,425_SY425_.jpg"
        result = _normalize_image_url(url)
        assert "._SX425_" not in result
        assert "._SY425_" not in result

    def test_url_not_from_amazon_unchanged(self):
        """非亚马逊来源的 URL 不会被误修改"""
        from extractor import _normalize_image_url

        url = "https://example.com/images/product_photo.jpg"
        result = _normalize_image_url(url)
        assert result == url


class TestExtractDetails:
    """提取产品详情。"""

    def test_extracts_feature_bullets(self):
        """从 #feature-bullets 提取要点列表"""
        from extractor import extract_product_info

        html = _load_fixture("amazon_product_sample.html")
        info = extract_product_info(html, "B0GVYXC124")
        assert "5-in-1 Multifunctional" in info["详情"]
        assert "Suction Massage" in info["详情"]
        assert "Dual Care" in info["详情"]

    def test_extracts_product_description(self):
        """同时提取 #productDescription 内容"""
        from extractor import extract_product_info

        html = _load_fixture("amazon_product_sample.html")
        info = extract_product_info(html, "B0GVYXC124")
        assert "Safe, Effective & Easy to Use" in info["详情"]
        assert "auto shut-off" in info["详情"]


class TestGracefulDegradation:
    """缺失字段时的优雅降级。"""

    def test_empty_html_returns_empty_fields(self):
        """空 HTML 返回所有字段为空字符串，不崩"""
        from extractor import extract_product_info

        info = extract_product_info("<html></html>", "B000000000")
        assert info["asin"] == "B000000000"
        assert info["标题"] == ""
        assert info["图片url"] == ""
        assert info["详情"] == ""

    def test_missing_images_returns_empty(self):
        """没有图片区域时返回空字符串"""
        html = '<html><body><div id="productTitle">Test</div></body></html>'
        from extractor import extract_product_info

        info = extract_product_info(html, "B000000000")
        assert info["图片url"] == ""

    def test_missing_details_returns_empty(self):
        """没有详情区域时返回空字符串"""
        html = '<html><body><div id="productTitle">Test</div></body></html>'
        from extractor import extract_product_info

        info = extract_product_info(html, "B000000000")
        assert info["详情"] == ""

    def test_asin_preserved_in_output(self):
        """输入的 ASIN 必须在返回结果中原样保留"""
        from extractor import extract_product_info

        html = _load_fixture("amazon_product_sample.html")
        info = extract_product_info(html, "B0GVYXC124")
        assert info["asin"] == "B0GVYXC124"
