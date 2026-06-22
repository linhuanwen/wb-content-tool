"""
测试 image_translator_ui.py — 图片翻译 UI 业务逻辑层。

原则：测试通过公共接口验证行为，mock 外部依赖。
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _reset_worker_manager():
    """每个测试前后重置 WorkerManager 单例，避免测试间状态泄漏。"""
    from worker import WorkerManager
    WorkerManager.reset()
    yield
    WorkerManager.reset()


# ============================================================
# 切片1: validate_image_upload — Excel 文件校验
# ============================================================


class TestValidateImageUpload:
    """上传 Excel 文件校验。"""

    def test_validate_4col_excel_returns_crawler_output(self, tmp_path):
        """上传爬虫 4 列 Excel，返回 input_type="crawler_output"，产品列表正确"""
        import openpyxl

        from image_translator_ui import validate_image_upload

        # 创建 4 列 Excel（爬虫输出格式）
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["asin", "图片url", "标题", "详情"])
        ws.append(["B0TEST1", "https://img1.jpg | https://img2.jpg", "Test Product", "Details here"])
        ws.append(["B0TEST2", "https://img3.jpg", "Another Product", "More details"])
        filepath = str(tmp_path / "test_4col.xlsx")
        wb.save(filepath)
        wb.close()

        result = validate_image_upload(filepath)

        assert result.is_valid is True
        assert result.input_type == "crawler_output"
        assert result.count == 2
        assert len(result.products) == 2
        assert result.products[0]["asin"] == "B0TEST1"
        assert result.products[0]["图片url"] == "https://img1.jpg | https://img2.jpg"

    def test_validate_12col_excel_returns_translation_output(self, tmp_path):
        """上传翻译后 12 列 Excel，返回 input_type="translation_output"，产品列表正确"""
        import openpyxl

        from image_translator_ui import validate_image_upload

        # 创建 12 列 Excel（翻译后格式）
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append([
            "asin", "图片url", "标题", "详情",
            "核心流量词", "俄语标题", "俄语详情",
            "货源", "采购价", "", "", "商品类别",
        ])
        ws.append([
            "B0T12COL", "https://img1.jpg", "Product X", "Details X",
            "ключевые слова", "Название", "Описание",
            "", "", "", "", "",
        ])
        filepath = str(tmp_path / "test_12col.xlsx")
        wb.save(filepath)
        wb.close()

        result = validate_image_upload(filepath)

        assert result.is_valid is True
        assert result.input_type == "translation_output"
        assert result.count == 1
        assert len(result.products) == 1
        assert result.products[0]["asin"] == "B0T12COL"

    def test_validate_rejects_missing_image_url_column(self, tmp_path):
        """缺少 图片url 列，返回 is_valid=False + 错误信息"""
        import openpyxl

        from image_translator_ui import validate_image_upload

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["asin", "标题", "详情"])  # 缺 图片url 列
        ws.append(["B0TEST1", "Product", "Details"])
        filepath = str(tmp_path / "test_missing_col.xlsx")
        wb.save(filepath)
        wb.close()

        result = validate_image_upload(filepath)

        assert result.is_valid is False
        assert result.error != ""
        assert "图片url" in result.error
        assert result.count == 0

    def test_validate_rejects_non_xlsx(self, tmp_path):
        """.csv 文件，返回 is_valid=False"""
        from image_translator_ui import validate_image_upload

        filepath = str(tmp_path / "test.csv")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("asin,图片url,标题,详情\nB01,url,Title,Details\n")

        result = validate_image_upload(filepath)

        assert result.is_valid is False
        assert "xlsx" in result.error.lower()
        assert result.count == 0


# ============================================================
# 切片2: generate_output_excel + generate_report — 下载生成
# ============================================================


class TestGenerateOutput:
    """输出 Excel 和报告生成。"""

    def test_generate_output_excel_urls_replaced(self):
        """验证输出 xlsx 中 r2图片url 列包含 R2 URL"""
        from image_translator import AsinImageResult, BatchImageResult, ImageResult
        from image_translator_ui import generate_output_excel

        # 构造 BatchImageResult
        images = [
            ImageResult(
                index=0,
                original_url="https://amazon.com/img1.jpg",
                r2_url="https://pub-xxx.r2.dev/B01/00_ru.jpg",
                status="ok",
            ),
            ImageResult(
                index=1,
                original_url="https://amazon.com/img2.jpg",
                r2_url="https://pub-xxx.r2.dev/B01/01_ru.jpg",
                status="skipped",
                error="OCR 失败",
            ),
        ]
        results = BatchImageResult(
            results=[
                AsinImageResult(
                    asin="B01",
                    images=images,
                    success_count=1,
                    error_count=0,
                    skipped_count=1,
                ),
            ],
            total_asins=1,
            completed_asins=1,
            total_images=2,
            success_images=1,
            skipped_images=1,
        )

        xlsx_bytes = generate_output_excel(results)

        assert isinstance(xlsx_bytes, bytes)
        assert len(xlsx_bytes) > 0

        # 验证内容：用 openpyxl 重新读取
        import io
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb.active

        # 表头
        headers = [cell.value for cell in ws[1]]
        assert "r2图片url" in headers

        # 数据行
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        assert len(rows) == 2  # 2 张图片

        # 验证 R2 URL 已写入
        r2_col = headers.index("r2图片url")
        row0_r2 = rows[0][r2_col]
        assert "r2.dev" in row0_r2

    def test_generate_report_contains_all_statuses(self):
        """报告含 ok/skipped/error 三类图片，验证每行列出了状态和 asin"""
        from image_translator import AsinImageResult, BatchImageResult, ImageResult
        from image_translator_ui import generate_report

        images = [
            ImageResult(index=0, original_url="url1", r2_url="r2_1", status="ok"),
            ImageResult(index=1, original_url="url2", r2_url="r2_2", status="skipped", error="OCR 失败"),
            ImageResult(index=2, original_url="url3", r2_url="url3", status="error", error="下载失败"),
        ]
        results = BatchImageResult(
            results=[
                AsinImageResult(asin="B0REPORT", images=images, success_count=1, error_count=1, skipped_count=1),
            ],
            total_asins=1,
            completed_asins=1,
            total_images=3,
            success_images=1,
            error_images=1,
            skipped_images=1,
        )

        csv_bytes = generate_report(results)

        assert isinstance(csv_bytes, bytes)
        report_text = csv_bytes.decode("utf-8")

        # 验证含所有三种状态
        assert "ok" in report_text
        assert "skipped" in report_text
        assert "error" in report_text
        assert "B0REPORT" in report_text
        # 验证错误信息列
        assert "OCR 失败" in report_text
        assert "下载失败" in report_text


# ============================================================
# 切片3: Worker 集成 — start/get_status/pause/resume
# ============================================================


class TestWorkerIntegration:
    """start_background_translation / get_worker_status / pause_resume。"""

    def test_get_worker_status_returns_idle_initially(self):
        """初始状态下 get_worker_status() 返回 idle"""
        from image_translator_ui import get_worker_status

        status = get_worker_status()
        assert status.state == "idle"

    def test_pause_and_resume_worker(self):
        """暂停后恢复 Worker"""
        from image_translator_ui import pause_worker, resume_worker

        # 暂停和恢复不应抛异常
        pause_worker()
        resume_worker()

    def test_start_background_translation_starts_worker(self):
        """start_background_translation 启动 Worker 并变更状态"""
        from image_processor import FontConfig, TextRegion
        from image_translator_ui import get_worker_status, start_background_translation

        # mock 函数让 Worker 快速完成
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
            {"asin": "B0UIINT", "图片url": "https://img1.jpg"},
        ]

        start_background_translation(
            products=products,
            font_config=FontConfig(),
        )

        # Worker 可能已经完成（很快），也可能还在运行
        import time
        time.sleep(0.3)

        status = get_worker_status()
        # 状态应为 running 或 completed
        assert status.state in ("running", "completed"), f"实际状态: {status.state}"
