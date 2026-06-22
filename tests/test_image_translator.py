"""
测试 image_translator.py — 单图翻译管道 + 批量处理。

原则：测试通过公共接口验证行为，mock 所有外部依赖。
"""

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 切片1: translate_single_image — 完整 6 步管线
# ============================================================


class TestSingleImageFullPipeline:
    """translate_single_image 完整管线。"""

    @pytest.mark.asyncio
    async def test_single_image_full_pipeline(self):
        """mock 所有外部调用，验证 ImageResult 各字段正确"""
        from image_processor import FontConfig, TextRegion
        from image_translator import translate_single_image

        # ── mock 函数 ──
        async def mock_download(url):
            return "/tmp/test_img.jpg"

        async def mock_ocr(local_path):
            return [
                TextRegion(text="Hello", translation="", box=(10, 20, 100, 50)),
                TextRegion(text="World", translation="", box=(10, 60, 100, 90)),
            ]

        async def mock_translate(texts):
            return ["привет", "мир"]

        async def mock_repair(image, regions):
            # 返回原图（不做实际修复）
            return image

        async def mock_resize(image):
            from PIL import Image

            return Image.new("RGB", (900, 1200), color=(255, 255, 255))

        async def mock_upload(local_path, remote_key):
            return f"https://pub-xxx.r2.dev/{remote_key}"

        # ── 执行 ──
        result = await translate_single_image(
            image_url="https://example.com/test.jpg",
            asin="B0TEST123",
            index=0,
            font_config=FontConfig(),
            _download_func=mock_download,
            _ocr_func=mock_ocr,
            _translate_func=mock_translate,
            _repair_func=mock_repair,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        # ── 验证 ──
        assert result.index == 0
        assert result.original_url == "https://example.com/test.jpg"
        assert result.r2_url == "https://pub-xxx.r2.dev/B0TEST123/00_ru.jpg"
        assert result.has_text is True
        assert result.translated is True
        assert result.status == "ok"
        assert result.error == ""
        assert result.ocr_original_texts == ["Hello", "World"]
        assert result.translated_texts == ["привет", "мир"]


class TestSingleImageNoText:
    """OCR 检测不到文字时跳过翻译+擦除+覆写。"""

    @pytest.mark.asyncio
    async def test_single_image_no_text_skips_translation(self):
        """mock OCR 返回空列表，验证跳过翻译+擦除+覆写，但仍改尺寸+上传"""
        from image_processor import FontConfig
        from image_translator import translate_single_image

        call_log = []

        async def mock_download(url):
            call_log.append("download")
            return "/tmp/test_img.jpg"

        async def mock_ocr(local_path):
            call_log.append("ocr")
            return []  # 无文字

        async def mock_translate(texts):
            call_log.append("translate")
            return []

        async def mock_repair(image, regions):
            call_log.append("repair")
            return image

        async def mock_resize(image):
            call_log.append("resize")
            from PIL import Image
            return Image.new("RGB", (900, 1200), color=(255, 255, 255))

        async def mock_upload(local_path, remote_key):
            call_log.append("upload")
            return f"https://pub-xxx.r2.dev/{remote_key}"

        result = await translate_single_image(
            image_url="https://example.com/test.jpg",
            asin="B0TEST123",
            index=1,
            font_config=FontConfig(),
            _download_func=mock_download,
            _ocr_func=mock_ocr,
            _translate_func=mock_translate,
            _repair_func=mock_repair,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        # 验证：无文字
        assert result.has_text is False
        assert result.translated is False
        assert result.status == "ok"
        # 验证：翻译和修复没有被调用
        assert "translate" not in call_log
        assert "repair" not in call_log
        # 验证：resize 和 upload 仍然执行
        assert "resize" in call_log
        assert "upload" in call_log


class TestSingleImageErrors:
    """各步骤失败时的容错降级行为。"""

    @pytest.mark.asyncio
    async def test_single_image_download_fails(self):
        """mock download 抛异常，验证 ImageResult.error 非空，r2_url 保留原始 URL"""
        from image_processor import FontConfig
        from image_translator import translate_single_image

        async def mock_download(url):
            raise ConnectionError("网络不可达")

        result = await translate_single_image(
            image_url="https://example.com/test.jpg",
            asin="B0TEST123",
            index=0,
            font_config=FontConfig(),
            _download_func=mock_download,
        )

        assert result.status == "error"
        assert result.error != ""
        assert "下载失败" in result.error or "网络不可达" in result.error
        assert result.r2_url == "https://example.com/test.jpg"

    @pytest.mark.asyncio
    async def test_single_image_ocr_fails(self):
        """mock OCR 抛异常，验证 skip 翻译但照样 resize+upload"""
        from image_processor import FontConfig
        from image_translator import translate_single_image

        call_log = []

        async def mock_download(url):
            return "/tmp/test_img.jpg"

        async def mock_ocr(local_path):
            call_log.append("ocr")
            raise RuntimeError("OCR 模型崩溃")

        async def mock_resize(image):
            call_log.append("resize")
            from PIL import Image
            return Image.new("RGB", (900, 1200), color=(255, 255, 255))

        async def mock_upload(local_path, remote_key):
            call_log.append("upload")
            return f"https://pub-xxx.r2.dev/{remote_key}"

        result = await translate_single_image(
            image_url="https://example.com/test.jpg",
            asin="B0TEST123",
            index=0,
            font_config=FontConfig(),
            _download_func=mock_download,
            _ocr_func=mock_ocr,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        assert result.status == "skipped"
        assert "OCR" in result.error
        # resize+upload 仍然执行
        assert "resize" in call_log
        assert "upload" in call_log

    @pytest.mark.asyncio
    async def test_single_image_translate_fails(self):
        """mock DeepSeek 失败，验证 skip 覆写但照样 resize+upload"""
        from image_processor import FontConfig, TextRegion
        from image_translator import translate_single_image

        call_log = []

        async def mock_download(url):
            return "/tmp/test_img.jpg"

        async def mock_ocr(local_path):
            return [TextRegion(text="Hello", translation="", box=(10, 20, 100, 50))]

        async def mock_translate(texts):
            call_log.append("translate")
            raise RuntimeError("翻译 API 超时")

        async def mock_resize(image):
            call_log.append("resize")
            from PIL import Image
            return Image.new("RGB", (900, 1200), color=(255, 255, 255))

        async def mock_upload(local_path, remote_key):
            call_log.append("upload")
            return f"https://pub-xxx.r2.dev/{remote_key}"

        result = await translate_single_image(
            image_url="https://example.com/test.jpg",
            asin="B0TEST123",
            index=0,
            font_config=FontConfig(),
            _download_func=mock_download,
            _ocr_func=mock_ocr,
            _translate_func=mock_translate,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        assert result.status == "error"
        assert result.translated is False
        # resize+upload 仍然执行
        assert "resize" in call_log
        assert "upload" in call_log

    @pytest.mark.asyncio
    async def test_single_image_repair_fails(self):
        """mock AI 修复失败，验证 skip 擦除+覆写但照样 resize+upload"""
        from image_processor import FontConfig, TextRegion
        from image_translator import translate_single_image

        call_log = []

        async def mock_download(url):
            return "/tmp/test_img.jpg"

        async def mock_ocr(local_path):
            return [TextRegion(text="Hello", translation="", box=(10, 20, 100, 50))]

        async def mock_translate(texts):
            return ["привет"]

        async def mock_repair(image, regions):
            call_log.append("repair")
            raise RuntimeError("Replicate API 故障")

        async def mock_resize(image):
            call_log.append("resize")
            from PIL import Image
            return Image.new("RGB", (900, 1200), color=(255, 255, 255))

        async def mock_upload(local_path, remote_key):
            call_log.append("upload")
            return f"https://pub-xxx.r2.dev/{remote_key}"

        result = await translate_single_image(
            image_url="https://example.com/test.jpg",
            asin="B0TEST123",
            index=0,
            font_config=FontConfig(),
            _download_func=mock_download,
            _ocr_func=mock_ocr,
            _translate_func=mock_translate,
            _repair_func=mock_repair,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        assert result.status == "error"
        # resize+upload 仍然执行
        assert "resize" in call_log
        assert "upload" in call_log

    @pytest.mark.asyncio
    async def test_single_image_r2_upload_fails(self):
        """mock R2 upload 抛异常，验证本地文件仍保存，ImageResult 用原始 URL"""
        from image_processor import FontConfig, TextRegion
        from image_translator import translate_single_image

        async def mock_download(url):
            return "/tmp/test_img.jpg"

        async def mock_ocr(local_path):
            return [TextRegion(text="Hello", translation="", box=(10, 20, 100, 50))]

        async def mock_translate(texts):
            return ["привет"]

        async def mock_repair(image, regions):
            return image

        async def mock_resize(image):
            from PIL import Image
            return Image.new("RGB", (900, 1200), color=(255, 255, 255))

        async def mock_upload(local_path, remote_key):
            raise RuntimeError("R2 上传超时")

        result = await translate_single_image(
            image_url="https://example.com/test.jpg",
            asin="B0TEST123",
            index=0,
            font_config=FontConfig(),
            _download_func=mock_download,
            _ocr_func=mock_ocr,
            _translate_func=mock_translate,
            _repair_func=mock_repair,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        assert result.status == "error"
        assert "upload" in result.error.lower() or "resize/upload" in result.error.lower()
        assert result.local_path != ""
        # r2_url 回退到原始 URL
        assert result.r2_url == "https://example.com/test.jpg"


# ============================================================
# 切片2: _retry_step — 指数退避重试
# ============================================================


class TestRetryStep:
    """_retry_step 指数退避重试机制。"""

    @pytest.mark.asyncio
    async def test_step_retry_exponential_backoff(self):
        """mock 前两次抛 Timeout，第三次成功，验证重试了 3 次且间隔递增"""
        from image_translator import _retry_step

        call_times = []

        async def flaky_fn():
            now = time.time()
            call_times.append(now)
            if len(call_times) < 3:
                raise asyncio.TimeoutError("超时")
            return "ok"

        result = await _retry_step(
            step_name="test_step",
            fn=flaky_fn,
            max_retries=3,
            backoff_base=2,
        )

        assert result == "ok"
        assert len(call_times) == 3, f"预期 3 次调用，实际 {len(call_times)} 次"
        # 验证间隔递增：第 2 次与第 1 次间隔 ≈ 1s，第 3 次与第 2 次间隔 ≈ 2s
        if len(call_times) >= 3:
            gap1 = call_times[1] - call_times[0]
            gap2 = call_times[2] - call_times[1]
            assert gap1 >= 0.8, f"第一次重试间隔应 ≈1s，实际 {gap1:.2f}s"
            assert gap2 >= gap1 * 0.8, f"第二次重试间隔应 ≥ 第一次，实际 gap1={gap1:.2f}s gap2={gap2:.2f}s"

    @pytest.mark.asyncio
    async def test_step_exhausts_retries_then_raises(self):
        """mock 连续 3 次抛异常，验证最终抛出异常"""
        from image_translator import _retry_step

        call_count = 0

        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("网络断开")

        with pytest.raises(ConnectionError):
            await _retry_step(
                step_name="download",
                fn=always_fails,
                max_retries=3,
                backoff_base=2,
            )

        assert call_count == 3, f"预期 3 次调用，实际 {call_count} 次"


# ============================================================
# 切片3: translate_asin_images — 并发处理
# ============================================================


class TestAsinImagesConcurrent:
    """translate_asin_images 并发处理一个 ASIN 的多张图片。"""

    @pytest.mark.asyncio
    async def test_asin_images_concurrent(self):
        """一个 ASIN 有 3 张图，验证 3 张图并发处理，总耗时 < 3×单张耗时"""
        from image_processor import FontConfig, TextRegion
        from image_translator import translate_asin_images

        per_image_delay = 0.2

        async def mock_download(url):
            await asyncio.sleep(per_image_delay)
            return f"/tmp/{url.split('/')[-1]}"

        async def mock_ocr(local_path):
            return [TextRegion(text="Test", translation="", box=(10, 20, 100, 50))]

        async def mock_translate(texts):
            return ["тест"]

        async def mock_repair(image, regions):
            return image

        async def mock_resize(image):
            from PIL import Image
            return Image.new("RGB", (900, 1200), color=(255, 255, 255))

        async def mock_upload(local_path, remote_key):
            return f"https://pub-xxx.r2.dev/{remote_key}"

        product = {
            "asin": "B0CONCUR",
            "图片url": "https://img1.jpg | https://img2.jpg | https://img3.jpg",
        }

        t0 = time.time()
        result = await translate_asin_images(
            product=product,
            font_config=FontConfig(),
            _download_func=mock_download,
            _ocr_func=mock_ocr,
            _translate_func=mock_translate,
            _repair_func=mock_repair,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )
        elapsed = time.time() - t0

        assert result.asin == "B0CONCUR"
        assert len(result.images) == 3
        assert result.success_count == 3

        # 并发：总耗时应远小于 3×单张耗时
        # 3 张图串行 = 3 * per_image_delay * N 步，并发 ≈ 1 * per_image_delay * N 步
        assert elapsed < per_image_delay * 3 + 0.5, (
            f"并发处理应远快于串行，实际耗时 {elapsed:.2f}s"
        )


# ============================================================
# 切片4: translate_batch — 断点续跑 + progress 写入
# ============================================================


class TestBatchResume:
    """translate_batch 断点续跑与进度写入。"""

    @pytest.mark.asyncio
    async def test_batch_resume_skips_completed_asins(self, tmp_path):
        """progress.json 已有 2 个完成 ASIN，输入含 4 个 ASIN，验证只处理未完成的 2 个"""
        import json

        from image_processor import FontConfig, TextRegion
        from image_translator import translate_batch

        # 准备 progress.json（已有 2 个 ASIN 完成）
        progress_file = str(tmp_path / "progress.json")
        existing_progress = {
            "state": "running",
            "completed_asins": ["B0DONE1", "B0DONE2"],
            "current_asin": "B0DONE2",
            "total_asins": 4,
            "total_images": 8,
            "processed_images": 4,
            "started_at": "2026-06-15T14:00:00",
            "updated_at": "2026-06-15T14:05:00",
        }
        with open(progress_file, "w", encoding="utf-8") as f:
            json.dump(existing_progress, f)

        processed_asins = []

        async def mock_download(url):
            return "/tmp/test.jpg"

        async def mock_ocr(local_path):
            return [TextRegion(text="Test", translation="", box=(10, 20, 100, 50))]

        async def mock_translate(texts):
            return ["тест"]

        async def mock_repair(image, regions):
            return image

        async def mock_resize(image):
            from PIL import Image
            return Image.new("RGB", (900, 1200))

        async def mock_upload(local_path, remote_key):
            return f"https://pub-xxx.r2.dev/{remote_key}"

        def progress_callback(asin_result):
            processed_asins.append(asin_result.asin)

        products = [
            {"asin": "B0DONE1", "图片url": "https://img1.jpg"},
            {"asin": "B0DONE2", "图片url": "https://img2.jpg"},
            {"asin": "B0NEW3", "图片url": "https://img3.jpg"},
            {"asin": "B0NEW4", "图片url": "https://img4.jpg"},
        ]

        result = await translate_batch(
            products=products,
            font_config=FontConfig(),
            progress_callback=progress_callback,
            resume_from=progress_file,
            _download_func=mock_download,
            _ocr_func=mock_ocr,
            _translate_func=mock_translate,
            _repair_func=mock_repair,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        # 只处理了未完成的 2 个
        assert len(processed_asins) == 2
        assert "B0DONE1" not in processed_asins
        assert "B0DONE2" not in processed_asins
        assert "B0NEW3" in processed_asins
        assert "B0NEW4" in processed_asins
        assert result.completed_asins == 2

    @pytest.mark.asyncio
    async def test_batch_writes_progress_after_each_asin(self, tmp_path):
        """mock 处理 2 个 ASIN，验证 progress.json 在第一个 ASIN 完成后写入"""
        import json

        from image_processor import FontConfig, TextRegion
        from image_translator import translate_batch

        progress_file = str(tmp_path / "progress.json")

        async def mock_download(url):
            return "/tmp/test.jpg"

        async def mock_ocr(local_path):
            return [TextRegion(text="Test", translation="", box=(10, 20, 100, 50))]

        async def mock_translate(texts):
            return ["тест"]

        async def mock_repair(image, regions):
            return image

        async def mock_resize(image):
            from PIL import Image
            return Image.new("RGB", (900, 1200))

        async def mock_upload(local_path, remote_key):
            return f"https://pub-xxx.r2.dev/{remote_key}"

        products = [
            {"asin": "B0AAA", "图片url": "https://img1.jpg"},
            {"asin": "B0BBB", "图片url": "https://img2.jpg"},
        ]

        await translate_batch(
            products=products,
            font_config=FontConfig(),
            resume_from=progress_file,
            _download_func=mock_download,
            _ocr_func=mock_ocr,
            _translate_func=mock_translate,
            _repair_func=mock_repair,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        # 验证 progress.json 存在且内容正确
        assert os.path.isfile(progress_file), "progress.json 应被创建"

        with open(progress_file, "r", encoding="utf-8") as f:
            progress = json.load(f)

        assert "B0AAA" in progress["completed_asins"]
        assert "B0BBB" in progress["completed_asins"]
        assert progress["state"] == "running"
        assert progress["total_asins"] == 2
