"""
测试 image_processor.py — 图片编辑引擎。

原则：测试通过公共接口验证行为，使用 PIL Image 真实创建/验证图片。
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 切片1: FontConfig + TextRegion dataclass
# ============================================================


class TestFontConfigDefaults:
    """FontConfig 默认值。"""

    def test_default_font_name(self):
        """默认字体为 Roboto-Regular.ttf"""
        from image_processor import FontConfig

        fc = FontConfig()
        assert fc.font_name == "Roboto-Regular.ttf"

    def test_default_auto_size(self):
        """默认启用自动字号"""
        from image_processor import FontConfig

        fc = FontConfig()
        assert fc.auto_size is True

    def test_default_color_mode(self):
        """默认颜色模式为 inherit（继承原文）"""
        from image_processor import FontConfig

        fc = FontConfig()
        assert fc.color_mode == "inherit"


class TestTextRegion:
    """TextRegion 数据类。"""

    def test_creates_text_region(self):
        """创建 TextRegion 并验证字段"""
        from image_processor import TextRegion

        region = TextRegion(
            text="Hello World",
            translation="привет мир",
            box=(10, 20, 200, 60),
        )
        assert region.text == "Hello World"
        assert region.translation == "привет мир"
        assert region.box == (10, 20, 200, 60)


class TestFontConfigAutoSize:
    """FontConfig 自动字号计算。"""

    def test_calculates_reasonable_size_for_typical_region(self):
        """给定 200×50 的文字区域，auto_size=True 计算合理字号"""
        from image_processor import FontConfig

        fc = FontConfig(auto_size=True)
        size = fc.calculate_size(
            text="привет мир",
            box_width=200,
            box_height=50,
        )
        # 字号应是正整数，且在合理范围内
        assert isinstance(size, int)
        assert size > 0
        assert size <= 50  # 不会超过区域高度

    def test_small_box_produces_small_font(self):
        """窄小区域产生小字号"""
        from image_processor import FontConfig

        fc = FontConfig(auto_size=True)
        size_small = fc.calculate_size(
            text="длинный текст на русском",
            box_width=60,
            box_height=20,
        )
        size_large = fc.calculate_size(
            text="длинный текст на русском",
            box_width=300,
            box_height=80,
        )
        assert size_small < size_large, "小区域字号应小于大区域"

    def test_manual_size_overrides_auto(self):
        """manual_size=True 时返回手动设置的字号"""
        from image_processor import FontConfig

        fc = FontConfig(auto_size=False, manual_size=18)
        size = fc.calculate_size(
            text="любой текст",
            box_width=200,
            box_height=50,
        )
        assert size == 18

    def test_returns_fallback_for_empty_text(self):
        """空文本时返回合理兜底字号"""
        from image_processor import FontConfig

        fc = FontConfig(auto_size=True)
        size = fc.calculate_size(
            text="",
            box_width=200,
            box_height=50,
        )
        assert isinstance(size, int)
        assert size > 0


# ============================================================
# 切片2: resize_to_3x4 — 等比缩放 + 白边填充到 3:4
# ============================================================


class TestResizeTo3x4:
    """resize_to_3x4 等比缩放+居中填充到 900×1200（3:4）。"""

    def test_resize_square_to_3x4(self):
        """1500×1500 方图 → 900×1200，等比缩放居中，上下白边"""
        from PIL import Image

        from image_processor import resize_to_3x4

        # 创建纯蓝色方图 1500×1500
        original = Image.new("RGB", (1500, 1500), color=(0, 100, 255))

        result = resize_to_3x4(original)

        # 输出尺寸
        assert result.size == (900, 1200), f"应为 900×1200，实际 {result.size}"

        # 上下应有白边（蓝色内容居中）
        # 1500 等比缩放至 900 宽: 1500 → 900 (scale=0.6), 高度 = 1500×0.6 = 900
        # 画布 1200，空白 = (1200 - 900) / 2 = 150
        # 所以第 0 行应是白色，第 150 行开始是蓝色
        top_pixel = result.getpixel((450, 5))  # 顶部中间，应在白边区
        assert top_pixel == (255, 255, 255), f"顶部应为白色，实际 {top_pixel}"

        center_pixel = result.getpixel((450, 600))  # 正中间，应为蓝色
        assert center_pixel == (0, 100, 255), f"中心应为蓝色，实际 {center_pixel}"

        bottom_pixel = result.getpixel((450, 1195))  # 底部中间，应在白边区
        assert bottom_pixel == (255, 255, 255), f"底部应为白色，实际 {bottom_pixel}"

    def test_resize_wide_to_3x4(self):
        """2000×1500 宽图 → 900×1200，内容无拉伸"""
        from PIL import Image

        from image_processor import resize_to_3x4

        original = Image.new("RGB", (2000, 1500), color=(255, 0, 0))

        result = resize_to_3x4(original)

        assert result.size == (900, 1200)

        # 宽图等比缩放以高度为准：1200/1500=0.8, 宽=2000×0.8=1600
        # 居中到 900 → 左右裁掉 (1600-900)/2 = 350px（在原图比例下）
        # 所以中心仍是红色
        center = result.getpixel((450, 600))
        assert center == (255, 0, 0), f"中心应为红色，实际 {center}"

    def test_resize_tall_to_3x4(self):
        """1000×2000 高图 → 900×1200，左右白边"""
        from PIL import Image

        from image_processor import resize_to_3x4

        original = Image.new("RGB", (1000, 2000), color=(0, 255, 0))

        result = resize_to_3x4(original)

        assert result.size == (900, 1200)

        # 高图等比缩放以宽度为准：900/1000=0.9, 高=2000×0.9=1800
        # 居中到 1200 → 上下裁掉 (1800-1200)/2 = 300px（在原图比例下）
        # 所以左右应有白边
        left_pixel = result.getpixel((5, 600))
        assert left_pixel == (255, 255, 255), f"左侧应为白色，实际 {left_pixel}"

        right_pixel = result.getpixel((895, 600))
        assert right_pixel == (255, 255, 255), f"右侧应为白色，实际 {right_pixel}"

    def test_resize_no_stretch(self):
        """验证内容无拉伸：缩放后像素等比例保持"""
        from PIL import Image

        from image_processor import resize_to_3x4

        # 创建一个有独特色彩图案的图
        original = Image.new("RGB", (800, 800), color=(100, 150, 200))
        # 画一个红色十字
        for x in range(390, 410):
            for y in range(800):
                original.putpixel((x, y), (255, 0, 0))
        for y in range(390, 410):
            for x in range(800):
                original.putpixel((x, y), (255, 0, 0))

        result = resize_to_3x4(original)

        assert result.size == (900, 1200)

        # 中心应仍是横竖交叉的红色（十字交叉点）
        center = result.getpixel((450, 600))
        assert center == (255, 0, 0), f"十字中心应为红色，实际 {center}"


# ============================================================
# 切片3: overlay_russian_text — 在图片上覆写俄文
# ============================================================


class TestOverlayRussianText:
    """overlay_russian_text 在 OCR 定位区域写入俄文。"""

    def test_writes_text_in_box_region(self):
        """验证俄文被写入 box 区域，区域内部像素发生变化"""
        from PIL import Image

        from image_processor import FontConfig, TextRegion, overlay_russian_text

        # 创建纯白图片 400×300
        original = Image.new("RGB", (400, 300), color=(255, 255, 255))

        # 定义文字区域（左上角 100×40）
        region = TextRegion(
            text="Hello",
            translation="привет",
            box=(50, 30, 250, 70),
        )

        result = overlay_russian_text(
            original,
            [region],
            FontConfig(color_mode="fixed", fixed_color="#000000"),
        )

        # 区域内部应该有像素变化（黑色文字出现在白底上）
        # 取 box 中心区域的像素
        cx, cy = 150, 50  # box 中心
        center_pixel = result.getpixel((cx, cy))

        # 区域内不应仍是纯白（文字被写入）
        has_dark_pixels = False
        for x in range(60, 240, 10):
            for y in range(35, 65, 5):
                px = result.getpixel((x, y))
                if px != (255, 255, 255):
                    has_dark_pixels = True
                    break
            if has_dark_pixels:
                break

        assert has_dark_pixels, "box 区域内应存在非白色像素（俄文被写入）"

    def test_empty_regions_returns_unchanged_image(self):
        """传入空 TextRegion 列表时图片完全不变"""
        from PIL import Image

        from image_processor import FontConfig, overlay_russian_text

        original = Image.new("RGB", (200, 200), color=(100, 150, 200))

        result = overlay_russian_text(original, [], FontConfig())

        # 逐像素对比（尺寸小，可以全量对比）
        for x in range(200):
            for y in range(200):
                assert result.getpixel((x, y)) == original.getpixel((x, y)), (
                    f"空 regions 时像素不应变化，但 ({x},{y}) 变了"
                )
