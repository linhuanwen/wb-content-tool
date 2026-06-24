"""
测试 image_generator.py — Gemini AI 图片卡片生成管道。

原则：测试通过公共接口验证行为，mock 所有外部依赖（下载/Gemini/上传）。
"""

import asyncio
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# 生成一个 1x1 像素的 JPEG 字节用于 mock Gemini 输出
def _fake_image_bytes() -> bytes:
    from io import BytesIO
    from PIL import Image

    buf = BytesIO()
    img = Image.new("RGB", (900, 1200), color=(200, 180, 160))
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue()


# ============================================================
# 切片1: generate_product_card — 完整 4 步管线
# ============================================================


class TestGenerateProductCardFullPipeline:
    """generate_product_card 完整管线（正常路径）。"""

    @pytest.mark.asyncio
    async def test_full_pipeline_generates_card(self):
        """mock 所有外部调用，验证 CardResult 各字段正确"""
        from image_generator import generate_product_card

        fake_bytes = _fake_image_bytes()

        async def mock_download(url):
            return "/tmp/test_product.jpg"

        async def mock_generate(image_path, product_context=None, *, mode="card_design", **kwargs):
            return fake_bytes, "使用家庭SPA场景背景，莫兰迪绿色调"

        async def mock_resize(image):
            from PIL import Image
            return image

        async def mock_upload(local_path, remote_key):
            return f"https://pub-xxx.r2.dev/{remote_key}"

        ctx = {
            "俄语标题": "массажер для лица 5 в 1",
            "核心流量词": "уход за кожей",
            "俄语详情": "очищает поры。увлажняет кожу。тонизирует мышцы",
            "product_category": "уход за кожей",
        }

        result = await generate_product_card(
            image_url="https://example.com/product.jpg",
            asin="B0TEST001",
            index=0,
            product_context=ctx,
            _download_func=mock_download,
            _generate_func=mock_generate,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        assert result.index == 0
        assert result.original_url == "https://example.com/product.jpg"
        assert "r2.dev/B0TEST001/00_card.jpg" in result.r2_url
        assert result.status == "ok"
        assert result.error == ""
        assert "SPA" in result.design_description
        assert result.local_path != ""


class TestGenerateProductCardErrors:
    """各步骤失败时的容错降级行为。"""

    @pytest.mark.asyncio
    async def test_download_fails_returns_error(self):
        """下载失败 → CardResult.error 非空，r2_url 回退原始 URL"""
        from image_generator import generate_product_card

        async def mock_download(url):
            raise ConnectionError("网络不可达")

        result = await generate_product_card(
            image_url="https://example.com/product.jpg",
            asin="B0TEST001",
            index=0,
            _download_func=mock_download,
        )

        assert result.status == "error"
        assert "下载失败" in result.error or "网络不可达" in result.error
        assert result.r2_url == "https://example.com/product.jpg"

    @pytest.mark.asyncio
    async def test_gemini_generate_fails_resize_upload_still_runs(self):
        """Gemini 生成失败 → 原图 resize + upload 仍然执行"""
        from image_generator import generate_product_card

        call_log = []

        async def mock_download(url):
            return "/tmp/test.jpg"

        async def mock_generate(image_path, product_context=None, *, mode="card_design", **kwargs):
            call_log.append("gemini")
            raise RuntimeError("Gemini API 500")

        async def mock_resize(image):
            call_log.append("resize")
            from PIL import Image
            return Image.new("RGB", (900, 1200))

        async def mock_upload(local_path, remote_key):
            call_log.append("upload")
            return f"https://pub-xxx.r2.dev/{remote_key}"

        result = await generate_product_card(
            image_url="https://example.com/product.jpg",
            asin="B0TEST001",
            index=0,
            _download_func=mock_download,
            _generate_func=mock_generate,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        assert result.status == "error"
        assert "gemini" in call_log
        assert "resize" in call_log
        assert "upload" in call_log

    @pytest.mark.asyncio
    async def test_gemini_returns_no_image(self):
        """Gemini 返回 (None, 错误说明)，status=error"""
        from image_generator import generate_product_card

        async def mock_download(url):
            return "/tmp/test.jpg"

        async def mock_generate(image_path, product_context=None, *, mode="card_design", **kwargs):
            return None, "API 限流，请稍后重试"

        result = await generate_product_card(
            image_url="https://example.com/product.jpg",
            asin="B0TEST001",
            index=0,
            _download_func=mock_download,
            _generate_func=mock_generate,
        )

        assert result.status == "error"
        assert "限流" in result.error or "未生成" in result.error

    @pytest.mark.asyncio
    async def test_upload_fails_local_file_still_saved(self):
        """R2 上传失败 → 本地文件仍保存，r2_url 回退原 URL"""
        from image_generator import generate_product_card

        fake_bytes = _fake_image_bytes()

        async def mock_download(url):
            return "/tmp/test.jpg"

        async def mock_generate(image_path, product_context=None, *, mode="card_design", **kwargs):
            return fake_bytes, "设计说明"

        async def mock_resize(image):
            return image

        async def mock_upload(local_path, remote_key):
            raise RuntimeError("R2 上传超时")

        result = await generate_product_card(
            image_url="https://example.com/product.jpg",
            asin="B0TEST001",
            index=0,
            _download_func=mock_download,
            _generate_func=mock_generate,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        assert result.status == "error"
        assert "上传" in result.error or "R2" in result.error
        assert result.local_path != ""
        assert result.r2_url == "https://example.com/product.jpg"


# ============================================================
# 切片2: generate_asin_cards — 并发处理
# ============================================================


class TestGenerateAsinCards:
    """generate_asin_cards 并发处理一个 ASIN 的多张图片。"""

    @pytest.mark.asyncio
    async def test_concurrent_card_generation(self):
        """3 张图并发生成，验证结果合并正确"""
        from image_generator import generate_asin_cards

        per_image_delay = 0.15
        fake_bytes = _fake_image_bytes()

        async def mock_download(url):
            await asyncio.sleep(per_image_delay)
            return f"/tmp/{url.split('/')[-1]}"

        async def mock_generate(image_path, product_context=None, *, mode="card_design", **kwargs):
            return fake_bytes, "тестовый дизайн"

        async def mock_resize(image):
            return image

        async def mock_upload(local_path, remote_key):
            return f"https://pub-xxx.r2.dev/{remote_key}"

        product = {
            "asin": "B0CONCUR",
            "图片url": "https://img1.jpg | https://img2.jpg | https://img3.jpg",
        }

        t0 = time.time()
        result = await generate_asin_cards(
            product=product,
            _download_func=mock_download,
            _generate_func=mock_generate,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )
        elapsed = time.time() - t0

        assert result.asin == "B0CONCUR"
        assert len(result.cards) == 3
        assert result.success_count == 3

        # 并发应远快于串行
        assert elapsed < per_image_delay * 3 + 0.5, (
            f"并发处理应远快于串行，实际耗时 {elapsed:.2f}s"
        )

    @pytest.mark.asyncio
    async def test_video_urls_filtered_out(self):
        """视频 URL 识别为 video 状态，不参与管线"""
        from image_generator import generate_asin_cards

        fake_bytes = _fake_image_bytes()

        async def mock_download(url):
            return "/tmp/test.jpg"

        async def mock_generate(image_path, product_context=None, *, mode="card_design", **kwargs):
            return fake_bytes, "design"

        async def mock_resize(image):
            return image

        async def mock_upload(local_path, remote_key):
            return f"https://pub-xxx.r2.dev/{remote_key}"

        product = {
            "asin": "B0VIDEO",
            "图片url": "https://img1.jpg | https://vse-vms-transcoding/video.m3u8 | https://img2.jpg",
        }

        result = await generate_asin_cards(
            product=product,
            _download_func=mock_download,
            _generate_func=mock_generate,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        # 共 3 个 URL，1 个视频，2 个图片
        assert len(result.cards) == 3
        assert result.video_count == 1
        assert result.success_count == 2


# ============================================================
# 切片3: generate_batch_cards — 断点续跑
# ============================================================


class TestGenerateBatchCards:
    """generate_batch_cards 批量处理与断点续跑。"""

    @pytest.mark.asyncio
    async def test_batch_resume_skips_completed(self, tmp_path):
        """card_progress.json 已有完成的 ASIN，跳过它们"""
        import json

        from image_generator import generate_batch_cards

        progress_file = str(tmp_path / "card_progress.json")
        existing = {
            "state": "running",
            "completed_asins": ["B0DONE1"],
            "current_asin": "B0DONE1",
            "total_asins": 3,
            "total_cards": 3,
            "processed_cards": 1,
            "started_at": "2026-06-20T10:00:00",
            "updated_at": "2026-06-20T10:05:00",
        }
        with open(progress_file, "w", encoding="utf-8") as f:
            json.dump(existing, f)

        processed = []
        fake_bytes = _fake_image_bytes()

        async def mock_download(url):
            return "/tmp/test.jpg"

        async def mock_generate(image_path, product_context=None, *, mode="card_design", **kwargs):
            return fake_bytes, "design"

        async def mock_resize(image):
            return image

        async def mock_upload(local_path, remote_key):
            return f"https://pub-xxx.r2.dev/{remote_key}"

        def callback(asin_result):
            processed.append(asin_result.asin)

        products = [
            {"asin": "B0DONE1", "图片url": "https://img1.jpg"},
            {"asin": "B0NEW2", "图片url": "https://img2.jpg"},
            {"asin": "B0NEW3", "图片url": "https://img3.jpg"},
        ]

        result = await generate_batch_cards(
            products=products,
            progress_callback=callback,
            resume_from=progress_file,
            _download_func=mock_download,
            _generate_func=mock_generate,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        assert len(processed) == 2
        assert "B0DONE1" not in processed
        assert "B0NEW2" in processed
        assert "B0NEW3" in processed
        assert result.completed_asins == 2

    @pytest.mark.asyncio
    async def test_batch_writes_card_progress(self, tmp_path):
        """每个 ASIN 完成后写入 card_progress.json"""
        import json

        from image_generator import generate_batch_cards

        progress_file = str(tmp_path / "card_progress.json")
        fake_bytes = _fake_image_bytes()

        async def mock_download(url):
            return "/tmp/test.jpg"

        async def mock_generate(image_path, product_context=None, *, mode="card_design", **kwargs):
            return fake_bytes, "design"

        async def mock_resize(image):
            return image

        async def mock_upload(local_path, remote_key):
            return f"https://pub-xxx.r2.dev/{remote_key}"

        products = [
            {"asin": "B0AA", "图片url": "https://img1.jpg"},
            {"asin": "B0BB", "图片url": "https://img2.jpg"},
        ]

        await generate_batch_cards(
            products=products,
            resume_from=progress_file,
            _download_func=mock_download,
            _generate_func=mock_generate,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        assert os.path.isfile(progress_file), "card_progress.json 应被创建"

        with open(progress_file, "r", encoding="utf-8") as f:
            progress = json.load(f)

        assert "B0AA" in progress["completed_asins"]
        assert "B0BB" in progress["completed_asins"]
        assert progress["state"] == "running"
        assert progress["total_asins"] == 2


# ============================================================
# 切片4: _build_design_prompt — Prompt 构建
# ============================================================


class TestBuildDesignPrompt:
    """_build_design_prompt 俄语文案 → 设计任务 Prompt。"""

    def test_full_prompt_contains_all_sections(self):
        """完整输入时 Prompt 包含标题/副标题/功能点/类别"""
        from image_generator import _build_design_prompt

        prompt = _build_design_prompt(
            title="массажер 5 в 1",
            subtitle="уход за кожей лица",
            features=["очищение пор", "увлажнение кожи", "лифтинг-эффект"],
            product_category="уход за кожей",
        )

        assert "массажер 5 в 1" in prompt
        assert "уход за кожей лица" in prompt
        assert "очищение пор" in prompt
        assert "увлажнение кожи" in prompt
        assert "лифтинг-эффект" in prompt
        assert "уход за кожей" in prompt
        assert "3:4" in prompt or "比例" in prompt

    def test_minimal_prompt_with_missing_fields(self):
        """缺字段时不崩溃，使用占位文本"""
        from image_generator import _build_design_prompt

        prompt = _build_design_prompt(
            title="",
            subtitle="",
            features=[],
            product_category="",
        )

        assert "待补充" in prompt


# ============================================================
# 切片5: _extract_features — 功能点提取
# ============================================================


class TestExtractFeatures:
    """_extract_features 从 product_context 提取 3 个功能点。"""

    def test_extracts_from_features_field(self):
        """从 features 字段提取（逗号分隔）"""
        from image_generator import _extract_features

        ctx = {"features": "Feature A, Feature B, Feature C, Feature D"}
        result = _extract_features(ctx)

        assert len(result) == 3
        assert "Feature A" in result

    def test_extracts_from_russian_description(self):
        """从俄语详情按句号拆分"""
        from image_generator import _extract_features

        ctx = {
            "俄语详情": "очищает поры。увлажняет кожу。тонизирует мышцы。снимает отеки",
        }
        result = _extract_features(ctx)

        assert len(result) == 3
        assert "очищает поры" in result

    def test_fallback_to_category_and_material(self):
        """无 features 和俄语详情时，回退到 category/material"""
        from image_generator import _extract_features

        ctx = {
            "product_category": "электроника",
            "material": "пластик",
        }
        result = _extract_features(ctx)

        assert len(result) >= 1


# ============================================================
# 切片7: TestTranslateMode — 图片翻译模式
# ============================================================


class TestTranslateMode:
    """验证 translate 模式使用简化 prompt，card_design 使用完整 prompt。"""

    @pytest.mark.asyncio
    async def test_translate_mode_passes_mode_through_pipeline(self):
        """translate 模式能正确通过管线传递 mode 参数"""
        from image_generator import (
            AsinCardResult,
            BatchCardResult,
            CardResult,
            generate_asin_cards,
            generate_batch_cards,
            generate_product_card,
        )

        fake_bytes = _fake_image_bytes()
        call_records: list[dict] = []

        async def mock_download(url):
            return "/tmp/test.jpg"

        async def mock_generate(image_path, product_context=None, *, mode="card_design", **kwargs):
            call_records.append({"image_path": image_path, "product_context": product_context, "mode": mode})
            if mode == "translate":
                return fake_bytes, "翻译完成"
            return fake_bytes, "卡片设计说明"

        async def mock_resize(image):
            return image

        async def mock_upload(local_path, remote_key):
            return f"https://pub-xxx.r2.dev/{remote_key}"

        # 测试 translate 模式
        result = await generate_product_card(
            image_url="https://example.com/product.jpg",
            asin="B0TRANS",
            index=0,
            product_context={"俄语标题": "Тест", "核心流量词": "ключ"},
            mode="translate",
            _download_func=mock_download,
            _generate_func=mock_generate,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        assert result.status == "ok"
        assert call_records[-1]["mode"] == "translate"
        assert "翻译完成" in result.design_description

    @pytest.mark.asyncio
    async def test_card_design_mode_is_default(self):
        """默认 mode 为 card_design，使用完整设计 prompt"""
        from image_generator import generate_product_card

        fake_bytes = _fake_image_bytes()
        call_records: list[dict] = []

        async def mock_download(url):
            return "/tmp/test.jpg"

        async def mock_generate(image_path, product_context=None, *, mode="card_design", **kwargs):
            call_records.append({"mode": mode})
            return fake_bytes, "设计说明：现代厨房场景"

        async def mock_resize(image):
            return image

        async def mock_upload(local_path, remote_key):
            return f"https://pub-xxx.r2.dev/{remote_key}"

        # 不传 mode，应默认 card_design
        await generate_product_card(
            image_url="https://example.com/product.jpg",
            asin="B0DEF",
            index=0,
            _download_func=mock_download,
            _generate_func=mock_generate,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        assert call_records[0]["mode"] == "card_design"

    @pytest.mark.asyncio
    async def test_translate_mode_through_batch_pipeline(self):
        """translate 模式通过 generate_batch_cards 正确传递"""
        from image_generator import generate_batch_cards

        fake_bytes = _fake_image_bytes()
        modes_seen: list[str] = []

        async def mock_download(url):
            return "/tmp/test.jpg"

        async def mock_generate(image_path, product_context=None, *, mode="card_design", **kwargs):
            modes_seen.append(mode)
            return fake_bytes, ""

        async def mock_resize(image):
            return image

        async def mock_upload(local_path, remote_key):
            return f"https://pub-xxx.r2.dev/{remote_key}"

        products = [
            {"asin": "B01", "图片url": "https://img.jpg"},
        ]

        await generate_batch_cards(
            products=products,
            mode="translate",
            _download_func=mock_download,
            _generate_func=mock_generate,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        assert len(modes_seen) >= 1
        assert all(m == "translate" for m in modes_seen)

    @pytest.mark.asyncio
    async def test_translate_mode_skips_ai_when_no_text_detected(self):
        """translate 模式下 OCR 检测无文字 → 跳过 AI 生成，直接缩放上传"""
        from unittest.mock import patch
        from image_generator import generate_product_card

        fake_bytes = _fake_image_bytes()
        call_log: list[str] = []

        async def mock_download(url):
            return "/tmp/test_no_text.jpg"

        async def mock_generate(image_path, product_context=None, *, mode="card_design", **kwargs):
            call_log.append("generate_called")
            return fake_bytes, ""

        async def mock_resize(image):
            call_log.append("resize_called")
            return image

        async def mock_upload(local_path, remote_key):
            call_log.append("upload_called")
            return f"https://pub-xxx.r2.dev/{remote_key}"

        # Patch _image_has_text 返回 False（模拟无文字）
        with patch("image_generator._image_has_text", return_value=False):
            result = await generate_product_card(
                image_url="https://example.com/product.jpg",
                asin="B0NOTEXT",
                index=0,
                mode="translate",
                _download_func=mock_download,
                _generate_func=mock_generate,
                _resize_func=mock_resize,
                _upload_func=mock_upload,
            )

        # AI 生成不应被调用
        assert "generate_called" not in call_log
        # resize 和 upload 应被调用
        assert "resize_called" in call_log
        assert "upload_called" in call_log
        # 结果状态
        assert result.status == "ok"
        assert "无文字" in result.design_description
        assert "pub-xxx.r2.dev" in result.r2_url
