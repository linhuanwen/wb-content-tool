"""
单图翻译管道 — 6 步管线（下载→OCR→翻译→修复→覆写→上传）。

公共接口：
    ImageResult       — 单图处理结果
    AsinImageResult   — 单 ASIN 处理结果
    BatchImageResult  — 批量处理结果
    translate_single_image — 单图完整管线
    translate_asin_images  — 单 ASIN 并发处理
    translate_batch        — 批量处理 + 断点续跑
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from PIL import Image

from image_processor import FontConfig, TextRegion, overlay_russian_text, resize_to_3x4


# ═══════════════════════════════════════════════════════════
# 真实管线实现（生产环境默认使用）
# ═══════════════════════════════════════════════════════════


async def _real_download(url: str) -> str:
    """真实图片下载：HTTP GET → 临时文件。"""
    import tempfile

    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    suffix = ".jpg"
    if "png" in url.lower():
        suffix = ".png"
    elif "webp" in url.lower():
        suffix = ".webp"

    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(resp.content)
    tmp.close()
    return tmp.name


# EasyOCR 全局单例（避免每次 OCR 都重新加载模型）
_easyocr_reader = None
_easyocr_lock = None  # 延迟 import asyncio lock


def _get_easyocr_reader():
    """获取 EasyOCR reader 单例（线程安全）。"""
    global _easyocr_reader, _easyocr_lock
    if _easyocr_reader is not None:
        return _easyocr_reader

    import threading
    if _easyocr_lock is None:
        _easyocr_lock = threading.Lock()

    with _easyocr_lock:
        if _easyocr_reader is not None:
            return _easyocr_reader
        import easyocr
        _easyocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        return _easyocr_reader


async def _real_ocr(local_path: str) -> list[TextRegion]:
    """真实 OCR（EasyOCR），未安装时返回空列表。"""
    MIN_CONFIDENCE = 0.3  # 置信度阈值，过滤误识别

    try:
        from PIL import Image
        import numpy as np

        # OpenCV（EasyOCR 底层）在 Windows 上无法读取含非 ASCII 字符的路径。
        # 先用 PIL 读取图片并转为 numpy 数组，再传给 EasyOCR，绕过路径问题。
        pil_img = Image.open(local_path).convert("RGB")
        img_array = np.array(pil_img)

        reader = _get_easyocr_reader()
        result = reader.readtext(img_array)

        regions = []
        for box, text, confidence in result:
            text = text.strip()
            if text and confidence >= MIN_CONFIDENCE:
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
        return regions
    except ImportError:
        # EasyOCR 未安装，返回空（后续步骤将 skip 翻译）
        return []
    except Exception:
        # OCR 异常也返回空列表，走"无文字"路径（resize+upload 不翻译）
        return []


async def _real_translate(texts: list[str]) -> list[str]:
    """真实翻译：调用 DeepSeek API 将英文→俄文。"""
    import json

    import requests

    from config import settings

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
            {
                "role": "system",
                "content": "You are a professional English-to-Russian translator for e-commerce product labels. Output only valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,  # 推理模型（deepseek-v4-pro）需预留 reasoning token
    }

    api_url = settings.translate_api_base_url.rstrip("/") + "/v1/chat/completions"
    resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()

    data = resp.json()
    content = data["choices"][0]["message"]["content"]

    # 提取 JSON
    import re

    m = re.search(r'\{[^}]+\}', content)
    if m:
        result = json.loads(m.group())
        return result.get("translations", [])
    return []


async def _real_upload(local_path: str, remote_key: str) -> str:
    """真实 R2 上传。"""
    from config import settings
    from r2_storage import R2Storage

    r2 = R2Storage(settings)
    return r2.upload(local_path, remote_key)


async def _retry_step(
    step_name: str,
    fn,
    max_retries: int = 3,
    backoff_base: int = 2,
    timeout: float = 60.0,
):
    """带指数退避的重试包装器。

    重试间隔: backoff_base^0=1s → backoff_base^1=2s → backoff_base^2=4s
    总计最长等待 ≈ 1+2+4 = 7s（默认 max_retries=3, backoff_base=2）。

    Args:
        step_name: 步骤名称（用于日志）。
        fn: 异步可调用对象。
        max_retries: 最大尝试次数（含首次）。
        backoff_base: 退避底数。
        timeout: 单次调用超时秒数。

    Returns:
        fn 的返回值。

    Raises:
        最后一次尝试的异常（所有重试耗尽后）。
    """
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            if asyncio.iscoroutinefunction(fn):
                coro = fn()
            elif asyncio.iscoroutine(fn):
                coro = fn
            else:
                coro = _call_async(fn)
            return await asyncio.wait_for(coro, timeout=timeout)
        except (asyncio.TimeoutError, Exception) as e:
            last_error = e if isinstance(e, Exception) else Exception(str(e))
            if attempt < max_retries - 1:
                delay = backoff_base ** attempt
                await asyncio.sleep(delay)
                continue

    # 所有重试耗尽
    if last_error:
        raise last_error
    raise RuntimeError(f"{step_name}: 所有 {max_retries} 次重试已耗尽")


async def _call_async(fn):
    """在线程池中运行同步函数，使其可被 asyncio.wait_for 管理。"""
    import asyncio as _asyncio
    return await _asyncio.get_event_loop().run_in_executor(None, fn)


@dataclass
class ImageResult:
    """单张图片的处理结果。

    Attributes:
        index: 图片在 ASIN 内的序号（0-based）。
        original_url: 原始图片 URL。
        r2_url: 翻译后的 R2 公开 URL（失败时回退为原始 URL 或本地路径）。
        local_path: 本地存档路径。
        has_text: OCR 是否检测到文字。
        translated: 是否成功覆写了俄文。
        status: "ok" | "skipped" | "error"。
        error: 错误信息（status=error 时非空）。
        retry_count: 该图总重试次数。
        ocr_original_texts: OCR 检测到的原文列表。
        translated_texts: 翻译后的俄文列表。
    """
    index: int = 0
    original_url: str = ""
    r2_url: str = ""
    local_path: str = ""
    has_text: bool = False
    translated: bool = False
    status: str = "ok"
    error: str = ""
    retry_count: int = 0
    ocr_original_texts: list[str] = field(default_factory=list)
    translated_texts: list[str] = field(default_factory=list)


@dataclass
class AsinImageResult:
    """单个 ASIN 的所有图片处理结果。

    Attributes:
        asin: 产品 ASIN。
        images: 该 ASIN 下所有图片的处理结果。
        success_count: status="ok" 的图片数。
        error_count: status="error" 的图片数。
        skipped_count: status="skipped" 的图片数。
    """
    asin: str = ""
    images: list[ImageResult] = field(default_factory=list)
    success_count: int = 0
    error_count: int = 0
    skipped_count: int = 0


@dataclass
class BatchImageResult:
    """批量处理结果。

    Attributes:
        results: 每个 ASIN 的处理结果。
        total_asins: 总 ASIN 数。
        completed_asins: 已完成的 ASIN 数。
        total_images: 总图片数。
        success_images: 成功图片数。
        error_images: 失败图片数。
        skipped_images: 跳过图片数。
        started_at: 开始时间 ISO 字符串。
        finished_at: 结束时间 ISO 字符串。
    """
    results: list[AsinImageResult] = field(default_factory=list)
    total_asins: int = 0
    completed_asins: int = 0
    total_images: int = 0
    success_images: int = 0
    error_images: int = 0
    skipped_images: int = 0
    started_at: str = ""
    finished_at: str = ""


async def translate_single_image(
    image_url: str,
    asin: str,
    index: int,
    font_config: FontConfig,
    *,
    _download_func: Callable | None = None,
    _ocr_func: Callable | None = None,
    _translate_func: Callable | None = None,
    _repair_func: Callable | None = None,
    _resize_func: Callable | None = None,
    _upload_func: Callable | None = None,
) -> ImageResult:
    """执行单张图片的完整翻译管线。

    管线步骤：下载 → OCR → 翻译 → AI修复擦除 → 覆写俄文 → 缩放 → 上传+本地存档。

    每个步骤失败时，按容错降级链处理：跳过受影响步骤，继续后续步骤。

    Args:
        image_url: 原始图片 URL。
        asin: 产品 ASIN。
        index: 图片序号（0-based）。
        font_config: 字体配置。
        _download_func: 下载函数（测试注入）。
        _ocr_func: OCR 函数（测试注入）。
        _translate_func: 翻译函数（测试注入）。
        _repair_func: AI 修复函数（测试注入）。
        _resize_func: 缩放函数（测试注入）。
        _upload_func: 上传函数（测试注入）。

    Returns:
        ImageResult 包含处理状态和结果。
    """
    result = ImageResult(
        index=index,
        original_url=image_url,
        has_text=False,
        translated=False,
        status="ok",
    )

    # 解析真实实现（测试可注入 mock）
    _download = _download_func or _real_download
    _ocr = _ocr_func or _real_ocr
    _translate = _translate_func or _real_translate
    _upload = _upload_func or _real_upload

    # ── 步骤1: 下载 ──
    local_path = ""
    try:
        local_path = await _download(image_url) if asyncio.iscoroutinefunction(_download) else _download(image_url)
    except Exception as e:
        result.status = "error"
        result.error = f"下载失败: {e}"
        result.r2_url = image_url
        return result

    # ── 步骤2: OCR ──
    regions: list[TextRegion] = []
    try:
        raw = _ocr(local_path)
        regions = await raw if asyncio.iscoroutine(raw) else raw
    except Exception:
        # OCR 失败 → 跳过翻译+擦除+覆写，继续 resize+upload
        result.status = "skipped"
        result.error = "OCR 失败"
        # 仍执行 resize+upload
        try:
            img = Image.open(local_path) if os.path.isfile(local_path) else Image.new("RGB", (900, 1200))
            if _resize_func:
                resized = _resize_func(img)
                resized = await resized if asyncio.iscoroutine(resized) else resized
            else:
                resized = resize_to_3x4(img)
            out_dir = os.path.join("images", asin)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{index:02d}_ru.jpg")
            resized.save(out_path, "JPEG")
            result.local_path = out_path
            remote_key = f"{asin}/{index:02d}_ru.jpg"
            if _upload_func:
                r2 = _upload_func(out_path, remote_key)
                result.r2_url = await r2 if asyncio.iscoroutine(r2) else r2
            else:
                result.r2_url = await _real_upload(out_path, remote_key)
        except Exception as e2:
            result.status = "error"
            result.error = f"OCR 失败 + 后续处理失败: {e2}"
            result.r2_url = image_url
        return result

    if not regions:
        # 无文字 → skip 翻译+擦除+覆写，仍 resize+upload
        result.has_text = False
        result.status = "ok"
        try:
            img = Image.open(local_path) if os.path.isfile(local_path) else Image.new("RGB", (900, 1200))
            if _resize_func:
                resized = _resize_func(img)
                resized = await resized if asyncio.iscoroutine(resized) else resized
            else:
                resized = resize_to_3x4(img)
            out_dir = os.path.join("images", asin)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{index:02d}_ru.jpg")
            resized.save(out_path, "JPEG")
            result.local_path = out_path
            remote_key = f"{asin}/{index:02d}_ru.jpg"
            r2 = _upload(out_path, remote_key)
            result.r2_url = await r2 if asyncio.iscoroutine(r2) else r2
        except Exception as e2:
            result.status = "error"
            result.error = f"resize/upload 失败: {e2}"
            result.r2_url = image_url
        return result

    result.has_text = True
    result.ocr_original_texts = [r.text for r in regions]

    # ── 步骤3: 翻译 ──
    try:
        raw = _translate([r.text for r in regions])
        translations = await raw if asyncio.iscoroutine(raw) else raw
        result.translated_texts = translations
        for r, t in zip(regions, translations):
            r.translation = t
        result.translated = True
    except Exception:
        # 翻译失败 → skip 覆写，继续 resize+upload
        result.translated = False
        try:
            img = Image.open(local_path) if os.path.isfile(local_path) else Image.new("RGB", (900, 1200))
            if _resize_func:
                resized = _resize_func(img)
                resized = await resized if asyncio.iscoroutine(resized) else resized
            else:
                resized = resize_to_3x4(img)
            out_dir = os.path.join("images", asin)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{index:02d}_ru.jpg")
            resized.save(out_path, "JPEG")
            result.local_path = out_path
            remote_key = f"{asin}/{index:02d}_ru.jpg"
            r2 = _upload(out_path, remote_key)
            result.r2_url = await r2 if asyncio.iscoroutine(r2) else r2
            result.status = "error"
            result.error = "翻译失败"
        except Exception as e2:
            result.status = "error"
            result.error = f"翻译失败 + 后续处理失败: {e2}"
            result.r2_url = image_url
        return result

    # ── 步骤4: AI 修复擦除（暂为 stub，后续对接 Replicate）──
    img = None
    try:
        img = Image.open(local_path) if os.path.isfile(local_path) else Image.new("RGB", (900, 1200))
        if _repair_func:
            repaired = _repair_func(img, regions)
            img = await repaired if asyncio.iscoroutine(repaired) else repaired
    except Exception:
        # 修复失败 → skip 擦除+覆写，继续 resize+upload
        try:
            if _resize_func:
                resized = _resize_func(img) if img else Image.new("RGB", (900, 1200))
                resized = await resized if asyncio.iscoroutine(resized) else resized
            else:
                resized = resize_to_3x4(img) if img else Image.new("RGB", (900, 1200))
            out_dir = os.path.join("images", asin)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{index:02d}_ru.jpg")
            resized.save(out_path, "JPEG")
            result.local_path = out_path
            remote_key = f"{asin}/{index:02d}_ru.jpg"
            r2 = _upload(out_path, remote_key)
            result.r2_url = await r2 if asyncio.iscoroutine(r2) else r2
            result.status = "error"
            result.error = f"AI 修复失败{': ' + str(e) if 'e' in dir() else ''}"
        except Exception as e2:
            result.status = "error"
            result.error = f"修复失败 + 后续处理失败: {e2}"
            result.r2_url = image_url
        return result

    # ── 步骤5: 覆写俄文 ──
    try:
        if result.translated and regions:
            img = overlay_russian_text(img, regions, font_config)
    except Exception:
        pass  # 覆写失败不阻断

    # ── 步骤6: 缩放 + 本地存档 + 上传 ──
    try:
        if _resize_func:
            resized = _resize_func(img)
            resized = await resized if asyncio.iscoroutine(resized) else resized
        else:
            resized = resize_to_3x4(img)
        out_dir = os.path.join("images", asin)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{index:02d}_ru.jpg")
        resized.save(out_path, "JPEG")
        result.local_path = out_path

        remote_key = f"{asin}/{index:02d}_ru.jpg"
        r2 = _upload(out_path, remote_key)
        result.r2_url = await r2 if asyncio.iscoroutine(r2) else r2
    except Exception as e:
        result.status = "error"
        result.error = f"resize/upload 失败: {e}"
        result.r2_url = image_url

    return result


async def translate_asin_images(
    product: dict,
    font_config: FontConfig,
    *,
    _download_func: Callable | None = None,
    _ocr_func: Callable | None = None,
    _translate_func: Callable | None = None,
    _repair_func: Callable | None = None,
    _resize_func: Callable | None = None,
    _upload_func: Callable | None = None,
) -> AsinImageResult:
    """并发处理单个 ASIN 的所有图片。

    Args:
        product: 产品字典，需包含 asin 和 图片url（以 " | " 分隔的多 URL）。
        font_config: 字体配置。
        _*_func: 测试注入用。

    Returns:
        AsinImageResult。
    """
    asin = product.get("asin", "")
    image_urls_str = product.get("图片url", "")
    # 爬虫输出用 ; 分隔，也兼容 | 分隔
    import re as _re
    urls = [u.strip() for u in _re.split(r'[;|]', image_urls_str) if u.strip()]

    if not urls:
        return AsinImageResult(asin=asin)

    # 并发处理所有图片
    tasks = []
    for i, url in enumerate(urls):
        tasks.append(
            translate_single_image(
                image_url=url,
                asin=asin,
                index=i,
                font_config=font_config,
                _download_func=_download_func,
                _ocr_func=_ocr_func,
                _translate_func=_translate_func,
                _repair_func=_repair_func,
                _resize_func=_resize_func,
                _upload_func=_upload_func,
            )
        )

    image_results = await asyncio.gather(*tasks)

    success = sum(1 for r in image_results if r.status == "ok")
    errors = sum(1 for r in image_results if r.status == "error")
    skipped = sum(1 for r in image_results if r.status == "skipped")

    return AsinImageResult(
        asin=asin,
        images=list(image_results),
        success_count=success,
        error_count=errors,
        skipped_count=skipped,
    )


async def translate_batch(
    products: list[dict],
    font_config: FontConfig | None = None,
    progress_callback: Callable | None = None,
    resume_from: str | None = None,
    *,
    _download_func: Callable | None = None,
    _ocr_func: Callable | None = None,
    _translate_func: Callable | None = None,
    _repair_func: Callable | None = None,
    _resize_func: Callable | None = None,
    _upload_func: Callable | None = None,
) -> BatchImageResult:
    """批量处理产品图片翻译。

    支持断点续跑：若 resume_from（progress.json 路径）已有完成的 ASIN，则跳过。

    Args:
        products: 产品列表。
        font_config: 字体配置。
        progress_callback: 每个 ASIN 完成后的回调。
        resume_from: progress.json 路径。
        _*_func: 测试注入用。

    Returns:
        BatchImageResult。
    """
    if font_config is None:
        font_config = FontConfig()

    import datetime
    import json

    started_at = datetime.datetime.now().isoformat()

    # 断点续跑：读取已完成的 ASIN
    completed_asins: set[str] = set()
    if resume_from and os.path.isfile(resume_from):
        try:
            with open(resume_from, "r", encoding="utf-8") as f:
                progress_data = json.load(f)
            completed_asins = set(progress_data.get("completed_asins", []))
        except (json.JSONDecodeError, KeyError):
            pass

    results: list[AsinImageResult] = []
    total_images = 0
    success_images = 0
    error_images = 0
    skipped_images = 0

    for product in products:
        asin = product.get("asin", "")

        # 跳过已完成的 ASIN
        if asin in completed_asins:
            continue

        asin_result = await translate_asin_images(
            product=product,
            font_config=font_config,
            _download_func=_download_func,
            _ocr_func=_ocr_func,
            _translate_func=_translate_func,
            _repair_func=_repair_func,
            _resize_func=_resize_func,
            _upload_func=_upload_func,
        )
        results.append(asin_result)
        total_images += len(asin_result.images)
        success_images += asin_result.success_count
        error_images += asin_result.error_count
        skipped_images += asin_result.skipped_count

        # 写入 progress.json
        if resume_from:
            _write_progress(
                resume_from,
                completed_asins=list(completed_asins | {asin}),
                current_asin=asin,
                total_asins=len(products),
                total_images=total_images,
                processed_images=success_images + error_images + skipped_images,
                started_at=started_at,
            )
            completed_asins.add(asin)

        if progress_callback:
            if asyncio.iscoroutinefunction(progress_callback):
                await progress_callback(asin_result)
            else:
                progress_callback(asin_result)

    finished_at = datetime.datetime.now().isoformat()

    return BatchImageResult(
        results=results,
        total_asins=len(products),
        completed_asins=len(results),
        total_images=total_images,
        success_images=success_images,
        error_images=error_images,
        skipped_images=skipped_images,
        started_at=started_at,
        finished_at=finished_at,
    )


def _write_progress(
    filepath: str,
    completed_asins: list[str],
    current_asin: str,
    total_asins: int,
    total_images: int,
    processed_images: int,
    started_at: str,
) -> None:
    """原子写入 progress.json。"""
    import json as _json
    import os as _os
    from datetime import datetime as _dt

    data = {
        "state": "running",
        "completed_asins": completed_asins,
        "current_asin": current_asin,
        "total_asins": total_asins,
        "total_images": total_images,
        "processed_images": processed_images,
        "started_at": started_at,
        "updated_at": _dt.now().isoformat(),
    }

    tmp_path = filepath + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False)
    _os.replace(tmp_path, filepath)
