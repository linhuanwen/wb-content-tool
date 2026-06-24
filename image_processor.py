"""
图片编辑引擎 — resize、文字擦除、俄文覆写。

公共接口：
    FontConfig     — 字体配置（字体名/字号/颜色）
    TextRegion     — OCR 文字区域（原文+译文+坐标）
    resize_to_3x4  — 等比缩放+白边填充到 900×1200
    overlay_russian_text — 在图片上覆写俄文
    erase_text_regions   — AI 修复擦除英文
"""

from __future__ import annotations

from dataclasses import dataclass
from PIL import Image


@dataclass
class FontConfig:
    """字体配置。

    Attributes:
        font_name: 字体文件名（需在系统字体目录或项目 fonts/ 目录下）。
        auto_size: True 时根据文字区域自动计算字号。
        manual_size: auto_size=False 时使用的手动字号。
        color_mode: 颜色模式 — "inherit"（继承原文）/ "auto"（自动对比度）/ "fixed"（固定颜色）。
        fixed_color: color_mode="fixed" 时的固定颜色。
    """
    font_name: str = "Roboto-Regular.ttf"
    auto_size: bool = True
    manual_size: int = 24
    color_mode: str = "inherit"
    fixed_color: str = "#000000"

    def calculate_size(self, text: str, box_width: int, box_height: int) -> int:
        """根据文字区域计算合适字号。

        Args:
            text: 要写入的文字。
            box_width: 文字区域宽度（像素）。
            box_height: 文字区域高度（像素）。

        Returns:
            计算后的字号（正整数）。
        """
        if not self.auto_size:
            return self.manual_size

        if not text:
            return max(8, box_height // 3)

        # 估算：每个字符约占字体宽度的 0.55（西里尔/拉丁字母平均）
        char_count = len(text)
        if char_count == 0:
            char_count = 1

        # 基于宽度计算
        size_by_width = int(box_width / (char_count * 0.55))
        # 基于高度计算（取 75% 的 box 高度，留白）
        size_by_height = int(box_height * 0.75)

        # 取较小值确保文字能放进区域，但不小于 6px
        size = min(size_by_width, size_by_height)
        return max(size, 6)


@dataclass
class TextRegion:
    """OCR 检测到的文字区域。

    Attributes:
        text: OCR 识别的原文（英文）。
        translation: AI 翻译后的俄文。
        box: 四角坐标 (x1, y1, x2, y2) 或四顶点 tuple。
    """
    text: str
    translation: str
    box: tuple


def resize_to_3x4(
    image: Image.Image,
    target_size: tuple[int, int] = (900, 1200),
    mode: str = "pad",
) -> Image.Image:
    """等比缩放图片并居中填充到目标画布。

    原则：绝不拉伸、不变形。原图等比缩放至 target_size 内最大适配尺寸，
    居中放置，空白区域白色填充。

    Args:
        image: PIL Image 对象。
        target_size: (宽, 高) 目标尺寸，默认 900×1200。
        mode: 填充模式 — "pad"（白边填充，当前唯一支持的模式）。

    Returns:
        处理后的 PIL Image。
    """
    tw, th = target_size

    # 计算等比缩放比例（适配画布内最大尺寸，不裁剪内容）
    scale = min(tw / image.width, th / image.height)

    # 按比例缩放原图
    new_w = int(image.width * scale)
    new_h = int(image.height * scale)
    resized = image.resize((new_w, new_h), Image.LANCZOS)

    # 创建白色画布
    canvas = Image.new("RGB", target_size, color=(255, 255, 255))

    # 居中粘贴
    paste_x = (tw - new_w) // 2
    paste_y = (th - new_h) // 2
    canvas.paste(resized, (paste_x, paste_y))

    return canvas


# 系统回退字体（支持西里尔字母的常见系统字体，按优先级排列）
_FALLBACK_FONT_NAMES = [
    "DejaVuSans.ttf",       # Linux
    "LiberationSans-Regular.ttf",  # Linux
    "Arial.ttf",            # Windows
    "arial.ttf",
    "FreeSans.ttf",         # Linux
    "Helvetica.ttf",        # macOS
    "SF-Pro.ttf",           # macOS
]


def _load_font(font_name: str, size: int):
    """加载字体，找不到时回退到系统字体，最后才用 Pillow 默认字体。

    Pillow 默认字体是极小位图字体，不支持西里尔/俄文字符，
    因此回退优先级：指定字体 → 系统常见字体 → Pillow 默认字体。

    Args:
        font_name: 字体文件名。
        size: 字号。

    Returns:
        PIL ImageFont 实例。
    """
    from PIL import ImageFont

    # 步骤 1: 直接按文件名加载（可能是绝对路径或当前目录文件）
    try:
        return ImageFont.truetype(font_name, size)
    except OSError:
        pass

    # 步骤 2: 从常见目录加载指定字体
    import os
    common_dirs = [
        os.path.join(os.path.dirname(__file__), "fonts"),
        "C:/Windows/Fonts",
        "/usr/share/fonts",
        "/usr/share/fonts/truetype",
        "/usr/share/fonts/truetype/dejavu",
        "/System/Library/Fonts",
    ]
    for d in common_dirs:
        path = os.path.join(d, font_name)
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue

    # 步骤 3: 回退到系统常见字体（支持 Cyrillic）
    for fallback in _FALLBACK_FONT_NAMES:
        for d in common_dirs:
            path = os.path.join(d, fallback)
            if os.path.isfile(path):
                try:
                    return ImageFont.truetype(path, size)
                except OSError:
                    continue

    # 步骤 4: 最终回退到 Pillow 默认字体（不支持俄文，仅最后手段）
    return ImageFont.load_default()


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """将 '#RRGGBB' 颜色字符串转为 (R, G, B) 元组。"""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _detect_background_color(
    image: Image.Image,
    x1: int, y1: int, x2: int, y2: int,
    padding: int = 4,
) -> tuple[int, int, int]:
    """检测文字区域的背景色。

    在文字区域外围采样像素，取出现次数最多的颜色作为背景色。

    Args:
        image: PIL Image 对象。
        x1, y1, x2, y2: 文字区域矩形。
        padding: 采样边界宽度（像素）。

    Returns:
        (R, G, B) 背景色元组。
    """
    from collections import Counter

    w, h = image.width, image.height

    # 扩大的采样区域
    x1_out = max(0, x1 - padding)
    y1_out = max(0, y1 - padding)
    x2_out = min(w, x2 + padding)
    y2_out = min(h, y2 + padding)

    pixels = image.load()
    colors: list[tuple[int, int, int]] = []

    # 上方外边框
    for x in range(x1_out, x2_out):
        for y in range(y1_out, y1):
            if 0 <= x < w and 0 <= y < h:
                colors.append(pixels[x, y][:3])

    # 下方外边框
    for x in range(x1_out, x2_out):
        for y in range(y2, y2_out):
            if 0 <= x < w and 0 <= y < h:
                colors.append(pixels[x, y][:3])

    # 左侧外边框
    for x in range(x1_out, x1):
        for y in range(y1, y2):
            if 0 <= x < w and 0 <= y < h:
                colors.append(pixels[x, y][:3])

    # 右侧外边框
    for x in range(x2, x2_out):
        for y in range(y1, y2):
            if 0 <= x < w and 0 <= y < h:
                colors.append(pixels[x, y][:3])

    if not colors:
        return (255, 255, 255)

    # 返回出现次数最多的颜色
    counter = Counter(colors)
    return counter.most_common(1)[0][0]


def _pick_text_color(bg_color: tuple[int, int, int]) -> tuple[int, int, int]:
    """根据背景亮度自动选择高对比度文字颜色。

    使用相对亮度公式（感知亮度），深色背景用白字，浅色背景用黑字。

    Args:
        bg_color: 背景色 (R, G, B)。

    Returns:
        文字颜色 (R, G, B) — 黑色 (0,0,0) 或白色 (255,255,255)。
    """
    r, g, b = bg_color
    # WCAG 相对亮度公式
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return (0, 0, 0) if luminance > 128 else (255, 255, 255)


def erase_text_regions(
    image: Image.Image,
    regions: list[TextRegion],
    margin: int = 10,
) -> Image.Image:
    """擦除图片中的文字区域（背景色填充 + 边缘扩展）。

    对每个 TextRegion，检测背景色后用该颜色填充扩大后的文字区域，
    确保英文原文被完全覆盖。扩展 margin 避免 OCR 框太紧导致残留。

    Args:
        image: 原图（会被原地修改并返回）。
        regions: 文字区域列表。
        margin: 擦除区域扩展像素（默认 10px），向四周各扩展。

    Returns:
        处理后的图片（与输入是同一个对象）。
    """
    if not regions:
        return image

    from PIL import ImageDraw

    w, h = image.width, image.height
    draw = ImageDraw.Draw(image)

    for region in regions:
        # 解析 box
        box = region.box
        if len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            x1, y1, x2, y2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        else:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x1, y1, x2, y2 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))

        # 扩展擦除区域（但不超过图片边界）
        x1e = max(0, x1 - margin)
        y1e = max(0, y1 - margin)
        x2e = min(w, x2 + margin)
        y2e = min(h, y2 + margin)

        # 检测背景色并填充
        bg_color = _detect_background_color(image, x1, y1, x2, y2)
        draw.rectangle([x1e, y1e, x2e, y2e], fill=bg_color)

    return image


def overlay_russian_text(
    image: Image.Image,
    regions: list[TextRegion],
    font_config: FontConfig | None = None,
) -> Image.Image:
    """在图片的指定区域覆写俄文。

    对每个 TextRegion，在 box 区域内写入 translation 译文。
    字号和颜色按 FontConfig 配置。

    Args:
        image: 原图（会被原地修改并返回）。
        regions: 文字区域列表。
        font_config: 字体配置，为 None 时使用默认配置。

    Returns:
        处理后的图片（与输入是同一个对象）。
    """
    if font_config is None:
        font_config = FontConfig()

    if not regions:
        return image

    from PIL import ImageDraw

    w_img, h_img = image.width, image.height
    # 擦除边界扩展量（OCR 框可能太紧，扩展确保完全覆盖英文）
    ERASE_MARGIN = 10

    # ═══════════════════════════════════════════════════════════
    # 阶段 0：预计算 — 从原始图片检测所有区域的背景色和布局参数
    #
    # 必须在任何擦除操作之前完成，否则相邻区域的擦除块会污染
    # 彼此的背景色采样，导致检测到错误的背景色、产生可见色块。
    # ═══════════════════════════════════════════════════════════
    _RegionPlan: list[dict] = []  # 每个 region 的预计算结果

    for region in regions:
        if not region.translation:
            continue

        # 解析 box 为矩形 (x1, y1, x2, y2)
        box = region.box
        if len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            x1, y1, x2, y2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        else:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x1, y1, x2, y2 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))

        box_w = x2 - x1
        box_h = y2 - y1

        # 扩展擦除区域（钳制到图片边界内）
        x1e = max(0, x1 - ERASE_MARGIN)
        y1e = max(0, y1 - ERASE_MARGIN)
        x2e = min(w_img, x2 + ERASE_MARGIN)
        y2e = min(h_img, y2 + ERASE_MARGIN)

        # 从原始图片检测背景色（此时图片尚未被任何擦除操作修改）
        bg_color = _detect_background_color(image, x1, y1, x2, y2)

        # 计算字号和字体
        size = font_config.calculate_size(region.translation, box_w, box_h)
        font = _load_font(font_config.font_name, size)

        # 确定文字颜色
        if font_config.color_mode == "fixed":
            color = _hex_to_rgb(font_config.fixed_color)
        else:
            color = _pick_text_color(bg_color)

        _RegionPlan.append({
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "box_w": box_w, "box_h": box_h,
            "x1e": x1e, "y1e": y1e, "x2e": x2e, "y2e": y2e,
            "bg_color": bg_color,
            "font": font,
            "color": color,
            "translation": region.translation,
        })

    # ═══════════════════════════════════════════════════════════
    # 阶段 1：统一擦除 — 所有区域先擦除原文
    #
    # 一次性擦除所有原文区域，避免逐区域擦除时后续区域的
    # 原文被前面区域的扩展擦除矩形覆盖。
    # ═══════════════════════════════════════════════════════════
    draw = ImageDraw.Draw(image)
    for plan in _RegionPlan:
        draw.rectangle(
            [plan["x1e"], plan["y1e"], plan["x2e"], plan["y2e"]],
            fill=plan["bg_color"],
        )

    # ═══════════════════════════════════════════════════════════
    # 阶段 2：统一绘制 — 所有俄文统一覆写
    #
    # 在所有原文被清除的干净画布上绘制俄文，不会出现新文字
    # 被后续擦除操作覆盖的问题。
    # ═══════════════════════════════════════════════════════════
    for plan in _RegionPlan:
        bbox = draw.textbbox((0, 0), plan["translation"], font=plan["font"])
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        text_x = plan["x1"] + (plan["box_w"] - text_w) // 2
        text_y = plan["y1"] + (plan["box_h"] - text_h) // 2

        draw.text(
            (text_x, text_y),
            plan["translation"],
            fill=plan["color"],
            font=plan["font"],
        )

    return image
