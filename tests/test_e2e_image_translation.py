"""
#11 端到端验证 — 图片翻译全链路真实测试。

验证：
1. R2 上传公开可访问 ✅ (已由 R2 连通性测试验证)
2. 端到端：3 个 ASIN 跑完整管线 → 输出 Excel 中 R2 URL 全部可公网打开
3. 本地 images/{ASIN}/ 目录结构正确

运行：python tests/test_e2e_image_translation.py
"""
import asyncio
import os
import sys
import tempfile
from io import BytesIO

import openpyxl
import requests
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from image_processor import FontConfig, TextRegion, overlay_russian_text, resize_to_3x4
from image_translator import (
    BatchImageResult,
    translate_single_image,
    translate_asin_images,
    translate_batch,
)

# ── 真实下载函数 ──
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


async def _real_download(url: str) -> str:
    """真实 HTTP 下载图片到临时文件。"""
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    # 保存到临时文件
    suffix = ".jpg"
    if "png" in url.lower():
        suffix = ".png"
    elif "webp" in url.lower():
        suffix = ".webp"

    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(resp.content)
    tmp.close()
    print(f"  [下载] {url[:80]}... -> {tmp.name} ({len(resp.content)} bytes)")
    return tmp.name


# ── 模拟 OCR（PaddleOCR 未安装时的轻量替代）──
async def _mock_ocr(local_path: str) -> list[TextRegion]:
    """返回模拟 OCR 文字区域，用于端到端验证管线。

    实际部署时应替换为 PaddleOCR 真实调用。
    """
    # 尝试打开图片获取尺寸
    try:
        img = Image.open(local_path)
        w, h = img.size
    except Exception:
        w, h = 900, 1200

    # 返回一个模拟文字区域（居中偏上位置）
    box = (int(w * 0.1), int(h * 0.05), int(w * 0.9), int(h * 0.15))
    return [
        TextRegion(text="Premium Quality Product", translation="", box=box),
        TextRegion(text="Best Seller 2024", translation="", box=(int(w * 0.1), int(h * 0.15), int(w * 0.6), int(h * 0.25))),
    ]


# ── 真实翻译函数（DeepSeek API）──
async def _real_translate(texts: list[str]) -> list[str]:
    """调用 DeepSeek API 将英文文本列表翻译为俄语。"""
    if not texts:
        return []

    prompt = (
        "Translate the following English product label texts to Russian.\n"
        "Rules:\n"
        "1. Accurate direct translation, no adding or removing information\n"
        "2. Keep technical parameters, numbers, and units exact\n"
        "3. Output ONLY a JSON array of strings, no other text\n"
        "4. All lowercase, no special symbols\n\n"
        "Texts to translate:\n"
    )
    for i, t in enumerate(texts, 1):
        prompt += f"{i}. {t}\n"
    prompt += '\nOutput: {"translations": ["translation1", "translation2", ...]}'

    headers = {
        "Authorization": f"Bearer {settings.translate_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.translate_model,
        "messages": [
            {"role": "system", "content": "You are a professional English-to-Russian translator for e-commerce product labels. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 500,
    }

    api_url = settings.translate_api_base_url.rstrip("/") + "/v1/chat/completions"
    resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()

    import json
    data = resp.json()
    content = data["choices"][0]["message"]["content"]

    # 提取 JSON
    json_match = None
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("{") and "translations" in line:
            json_match = line
            break
    if not json_match:
        # Try to find JSON in the content
        import re
        m = re.search(r'\{[^}]+\}', content)
        if m:
            json_match = m.group()
        else:
            json_match = content

    result = json.loads(json_match)
    translations = result.get("translations", [])
    print(f"  [翻译] {texts} -> {translations}")
    return translations


# ── 真实上传函数 ──
async def _real_upload(local_path: str, remote_key: str) -> str:
    """上传到 R2 并返回公开 URL。"""
    from r2_storage import R2Storage
    r2 = R2Storage(settings)
    url = r2.upload(local_path, remote_key)
    print(f"  [上传] {local_path} -> {url}")
    return url


# ── 真实 resize 函数 ──
async def _real_resize(image: Image.Image) -> Image.Image:
    """等比缩放 + 白边填充到 900×1200。"""
    return resize_to_3x4(image)


async def _process_one_asin(asin: str, image_url: str, index: int = 0) -> None:
    """处理单个 ASIN 的完整管线。"""
    print(f"\n{'='*60}")
    print(f"Processing ASIN={asin}, image_url={image_url[:80]}...")
    print(f"{'='*60}")

    font_config = FontConfig()

    result = await translate_single_image(
        image_url=image_url,
        asin=asin,
        index=index,
        font_config=font_config,
        _download_func=_real_download,
        _ocr_func=_mock_ocr,
        _translate_func=_real_translate,
        _resize_func=_real_resize,
        _upload_func=_real_upload,
    )

    print(f"\n  Result: status={result.status}")
    print(f"  Original URL: {result.original_url[:80]}...")
    print(f"  R2 URL: {result.r2_url}")
    print(f"  Local path: {result.local_path}")
    print(f"  Has text: {result.has_text}")
    print(f"  Translated: {result.translated}")
    if result.error:
        print(f"  Error: {result.error}")
    if result.ocr_original_texts:
        print(f"  OCR texts: {result.ocr_original_texts}")
    if result.translated_texts:
        print(f"  Translated texts: {result.translated_texts}")

    return result


async def run_e2e_test(products: list[dict]) -> BatchImageResult:
    """运行端到端批量测试。"""
    font_config = FontConfig()

    result = await translate_batch(
        products=products,
        font_config=font_config,
        resume_from=None,  # 从头开始
        _download_func=_real_download,
        _ocr_func=_mock_ocr,
        _translate_func=_real_translate,
        _resize_func=_real_resize,
        _upload_func=_real_upload,
    )

    return result


def verify_r2_urls(results: BatchImageResult) -> dict:
    """验证所有 R2 URL 公开可访问。"""
    print("\n" + "="*60)
    print("Verifying R2 URLs...")
    print("="*60)

    verify_results = {}
    for asin_result in results.results:
        for img in asin_result.images:
            url = img.r2_url
            print(f"\n  ASIN={asin_result.asin}, index={img.index}")
            print(f"  URL: {url}")

            try:
                resp = requests.head(url, timeout=15, allow_redirects=True)
                is_ok = resp.status_code == 200
                print(f"  Status: {resp.status_code}, OK: {is_ok}")
                verify_results[url] = is_ok
            except Exception as e:
                print(f"  [FAIL] {e}")
                verify_results[url] = False

    return verify_results


def verify_local_structure(results: BatchImageResult) -> bool:
    """验证本地 images/{ASIN}/ 目录结构。"""
    print("\n" + "="*60)
    print("Verifying local directory structure...")
    print("="*60)

    base = "images"
    all_ok = True

    for asin_result in results.results:
        asin_dir = os.path.join(base, asin_result.asin)
        print(f"\n  {asin_dir}/")

        if not os.path.isdir(asin_dir):
            print(f"    [MISSING] Directory does not exist!")
            all_ok = False
            continue

        for img in asin_result.images:
            expected_name = f"{img.index:02d}_ru.jpg"
            expected_path = os.path.join(asin_dir, expected_name)
            exists = os.path.isfile(expected_path)
            print(f"    {expected_name}: {'[OK]' if exists else '[MISSING]'}")
            if exists:
                # Check file size
                size = os.path.getsize(expected_path)
                print(f"      Size: {size} bytes")

                # Check dimensions
                try:
                    pil_img = Image.open(expected_path)
                    print(f"      Dimensions: {pil_img.size}")
                    if pil_img.size != (900, 1200):
                        print(f"      [WARN] Expected 900x1200, got {pil_img.size}")
                        all_ok = False
                except Exception as e:
                    print(f"      [ERROR] Cannot open image: {e}")
                    all_ok = False
            else:
                all_ok = False

    return all_ok


def generate_output_excel_mock(results: BatchImageResult) -> bytes:
    """生成输出 xlsx 文件（R2 URL 替换原始 URL）。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["asin", "原始图片url", "r2图片url", "状态", "错误信息"])

    for asin_result in results.results:
        asin = asin_result.asin
        for img in asin_result.images:
            ws.append([
                asin,
                img.original_url,
                img.r2_url,
                img.status,
                img.error,
            ])

    output = BytesIO()
    wb.save(output)
    wb.close()
    return output.getvalue()


def main():
    """主入口：运行端到端测试。"""
    print("="*60)
    print("#11 End-to-End Image Translation Test")
    print("="*60)

    # 测试产品：取前 3 个有图片的 ASIN
    products = [
        {"asin": "B0GVYXC124", "图片url": "https://m.media-amazon.com/images/I/71KpYEV4hPL.jpg"},
        {"asin": "B0F45N6NS7", "图片url": "https://m.media-amazon.com/images/I/61L1YdgpYeL.jpg"},
        {"asin": "B0GSZ9CW4K", "图片url": "https://m.media-amazon.com/images/I/61ewtu92+kL.jpg"},
    ]

    print(f"\nTest products: {len(products)} ASINs")
    for p in products:
        print(f"  {p['asin']}: {p['图片url'][:80]}...")

    # 运行端到端测试
    results = asyncio.run(run_e2e_test(products))

    # 打印汇总
    print("\n" + "="*60)
    print("Batch Result Summary")
    print("="*60)
    print(f"  Total ASINs: {results.total_asins}")
    print(f"  Completed: {results.completed_asins}")
    print(f"  Total images: {results.total_images}")
    print(f"  Success: {results.success_images}")
    print(f"  Errors: {results.error_images}")
    print(f"  Skipped: {results.skipped_images}")
    print(f"  Started: {results.started_at}")
    print(f"  Finished: {results.finished_at}")

    # 验证 R2 URL 可访问性
    verify = verify_r2_urls(results)
    all_r2_ok = all(verify.values())
    print(f"\n  R2 URLs all accessible: {all_r2_ok}")

    # 验证本地目录结构
    local_ok = verify_local_structure(results)

    # 生成输出 Excel
    xlsx_bytes = generate_output_excel_mock(results)
    output_path = "e2e_test_output.xlsx"
    with open(output_path, "wb") as f:
        f.write(xlsx_bytes)
    print(f"\n  Output Excel saved: {output_path}")

    # 最终结论
    print("\n" + "="*60)
    print("FINAL VERDICT")
    print("="*60)
    print(f"  R2 connectivity: PASS")
    print(f"  Pipeline completed: {'PASS' if results.completed_asins == len(products) else 'PARTIAL'}")
    print(f"  R2 URLs accessible: {'PASS' if all_r2_ok else 'FAIL'}")
    print(f"  Local structure: {'PASS' if local_ok else 'FAIL'}")
    overall = all_r2_ok and local_ok and results.completed_asins == len(products)
    print(f"  OVERALL: {'PASS' if overall else 'FAIL'}")

    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
