"""
可视化 Demo — 创建带英文字的测试图，跑完整管线（mock OCR + 真实翻译 + 真实覆写）。

输出：
  demo_before.jpg — 原始测试图（英文字）
  demo_after.jpg  — 处理后的图（俄文字 + 900×1200）
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw, ImageFont

from image_processor import FontConfig, TextRegion


def create_test_image() -> str:
    """创建一张白底带英文字的测试图，模拟亚马逊产品图上的文字。"""
    img = Image.new("RGB", (1500, 1500), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    # 尝试使用系统字体，失败则用默认
    try:
        # Windows 常用字体
        font_large = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 80)
        font_medium = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 50)
        font_small = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 36)
    except Exception:
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # 画产品名称（模拟主图文字）
    draw.text((100, 80), "Premium Quality", fill=(0, 100, 200), font=font_large)
    draw.text((100, 200), "Wireless Headphones", fill=(0, 100, 200), font=font_large)

    # 画卖点标签
    draw.rectangle([100, 360, 500, 440], fill=(255, 200, 0))
    draw.text((120, 368), "Best Seller 2024", fill=(0, 0, 0), font=font_medium)

    # 画特性说明
    draw.text((100, 520), "Noise Cancelling Technology", fill=(50, 50, 50), font=font_small)
    draw.text((100, 580), "40 Hours Battery Life", fill=(50, 50, 50), font=font_small)
    draw.text((100, 640), "Bluetooth 5.3 | USB-C | Foldable", fill=(50, 50, 50), font=font_small)

    # 画一个模拟产品图的矩形框
    draw.rectangle([600, 80, 1350, 900], outline=(200, 200, 200), width=3)
    draw.text((800, 450), "Product Image", fill=(180, 180, 180), font=font_large)

    # 底部价格标签
    draw.rectangle([100, 800, 450, 900], fill=(0, 150, 0))
    draw.text((120, 810), "SPECIAL OFFER", fill=(255, 255, 255), font=font_small)
    draw.text((120, 850), "$49.99", fill=(255, 255, 255), font=font_large)

    path = "demo_before.jpg"
    img.save(path, "JPEG", quality=95)
    print(f"Created test image: {path} ({img.size})")
    return path


async def process_demo():
    """跑完整管线。"""
    from image_translator import translate_single_image
    from image_processor import resize_to_3x4, overlay_russian_text

    # ── 步骤1: 创建测试图 ──
    local_path = create_test_image()

    # ── 步骤2: 真实 OCR (EasyOCR) ──
    from image_translator import _get_easyocr_reader

    reader = _get_easyocr_reader()
    raw_results = reader.readtext(local_path)

    regions = []
    MIN_CONF = 0.3
    for box, text, confidence in raw_results:
        text = text.strip()
        if text and confidence >= MIN_CONF:
            x_coords = [p[0] for p in box]
            y_coords = [p[1] for p in box]
            regions.append(
                TextRegion(
                    text=text,
                    translation="",
                    box=(int(min(x_coords)), int(min(y_coords)),
                         int(max(x_coords)), int(max(y_coords))),
                )
            )

    print(f"  1. OCR (EasyOCR): {len(regions)} text regions")
    for r in regions:
        print(f"     - \"{r.text}\" @ {r.box}")

    # ── 步骤3: 真实翻译 (DeepSeek) ──
    async def real_translate(texts):
        import json
        import re
        import requests
        from config import settings

        prompt = (
            "Translate the following English product labels to Russian.\n"
            "Rules: accurate direct translation, keep numbers/units exact.\n"
            "Output ONLY a JSON array of strings, no other text.\n\n"
            "Texts:\n"
        )
        for i, t in enumerate(texts, 1):
            prompt += f'{i}. "{t}"\n'
        prompt += '\nOutput: {"translations": ["...", ...]}'

        resp = requests.post(
            settings.translate_api_base_url.rstrip("/") + "/v1/chat/completions",
            json={
                "model": settings.translate_model,
                "messages": [
                    {"role": "system", "content": "You are a professional English→Russian translator. Output only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 4096,
            },
            headers={"Authorization": f"Bearer {settings.translate_api_key}", "Content-Type": "application/json"},
            timeout=30,
        )
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        m = re.search(r'\{[^}]+\}', content)
        result = json.loads(m.group()) if m else {"translations": texts}
        translations = result.get("translations", texts)
        print(f"  Translate: {texts} -> {translations}")
        return translations

    # ── 步骤4: 覆写俄文 ──
    print("\nRunning pipeline...")
    translations = await real_translate([r.text for r in regions])

    for r, t in zip(regions, translations):
        r.translation = t

    # ── 步骤5: 覆写俄文 ──
    img = Image.open(local_path)
    font_config = FontConfig(font_name="C:/Windows/Fonts/arial.ttf", auto_size=True)
    try:
        img = overlay_russian_text(img, regions, font_config)
        print(f"  2. Overlay: Russian text written on image")
    except Exception as e:
        print(f"  2. Overlay: FAILED - {e}")

    # ── 步骤6: 缩放 ──
    resized = resize_to_3x4(img)
    resized.save("demo_after.jpg", "JPEG", quality=95)
    print(f"  3. Resize: {img.size} -> {resized.size} (900x1200)")
    print(f"  4. Output: demo_after.jpg")

    print("\nDone! Compare:")
    print("  Before: demo_before.jpg (1500x1500, English text)")
    print("  After:  demo_after.jpg  (900x1200, Russian text)")


if __name__ == "__main__":
    asyncio.run(process_demo())
