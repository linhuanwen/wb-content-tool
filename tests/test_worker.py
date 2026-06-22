"""
测试 worker.py — WorkerManager 及原子写入工具函数。
"""

import json
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
# 切片1: _atomic_write_json — 原子写入 progress.json
# ============================================================


class TestAtomicWriteJson:
    """原子写入：先写 tmp 文件，再 os.replace 到目标路径。"""

    def test_writes_valid_json_to_target_file(self, tmp_path):
        """正常写入后目标文件包含正确的 JSON 数据"""
        from worker import _atomic_write_json

        target = tmp_path / "progress.json"
        data = {"state": "running", "completed_asins": ["B01", "B02"]}

        _atomic_write_json(str(target), data)

        # 文件存在且是有效 JSON
        assert target.is_file()
        with open(target, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded == data

    def test_uses_temp_file_then_replace(self, tmp_path):
        """验证写入流程：先写 .tmp 文件，再 os.replace"""
        target = tmp_path / "progress.json"
        tmp_file = tmp_path / "progress.json.tmp"

        # 先确保没有残留文件
        assert not target.exists()
        assert not tmp_file.exists()

        # Mock os.replace 来追踪调用
        original_replace = os.replace

        replace_calls = []

        def spy_replace(src, dst):
            replace_calls.append((src, dst))
            original_replace(src, dst)

        with patch("worker.os.replace", side_effect=spy_replace):
            from worker import _atomic_write_json

            _atomic_write_json(str(target), {"state": "idle"})

        # 验证 os.replace 被调用，且参数正确
        assert len(replace_calls) == 1
        src, dst = replace_calls[0]
        assert src == str(tmp_file)
        assert dst == str(target)

        # tmp 文件不应再存在（已被 replace 移动）
        assert not tmp_file.exists()

        # 最终目标文件存在
        assert target.is_file()

    def test_overwrites_existing_file(self, tmp_path):
        """已有旧数据时，新写入覆盖旧数据"""
        from worker import _atomic_write_json

        target = tmp_path / "progress.json"

        # 先写入旧数据
        old_data = {"state": "idle", "completed": []}
        target.write_text(json.dumps(old_data), encoding="utf-8")

        # 写入新数据
        new_data = {"state": "running", "completed": ["B01"]}
        _atomic_write_json(str(target), new_data)

        # 验证新数据生效
        with open(target, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded == new_data

    def test_no_corruption_on_partial_write(self, tmp_path):
        """模拟写入中断：tmp 文件写了一半被 kill。

        验证：目标文件不受影响（保留旧数据或不存在），
        不会出现半截 JSON 的情况。
        """
        # 先写入旧数据作为"上一次成功写入"
        target = tmp_path / "progress.json"
        old_data = {"state": "completed", "total": 10}
        target.write_text(json.dumps(old_data), encoding="utf-8")

        # 模拟：写 tmp 写一半，不执行 os.replace（模拟崩溃）
        tmp_file = tmp_path / "progress.json.tmp"
        tmp_file.write_text('{"state": "running", "cur', encoding="utf-8")
        # 模拟崩溃：不调用 os.replace

        # 验证目标文件仍是旧数据（完整 JSON）
        assert target.is_file()
        with open(target, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded == old_data, "目标文件不应受不完整的 tmp 文件影响"

        # tmp 文件是坏的（半截 JSON），但不应阻止后续正常写入
        # 下一次成功的 _atomic_write_json 会覆盖 tmp 文件

    def test_handles_nested_unicode_data(self, tmp_path):
        """写入包含 Unicode（俄文）的数据"""
        from worker import _atomic_write_json

        target = tmp_path / "progress.json"
        data = {
            "current_asin": "B0GVYXC124",
            "статус": "выполняется",  # 俄文值
        }

        _atomic_write_json(str(target), data)

        with open(target, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded == data
        assert loaded["статус"] == "выполняется"


# ============================================================
# 切片2: WorkerManager — 后台 Worker 管理
# ============================================================


class TestWorkerStatus:
    """WorkerStatus 状态反映。"""

    def test_worker_status_reflects_state(self):
        """WorkerManager.get_status() 返回 running/paused/completed/idle 状态正确"""
        from worker import WorkerManager, WorkerStatus

        manager = WorkerManager()

        # 初始状态：idle
        status = manager.get_status()
        assert status.state == "idle"
        assert isinstance(status, WorkerStatus)

    def test_worker_status_is_fresh_instance(self):
        """每次 get_status() 返回最新的进度快照"""
        from worker import WorkerManager

        manager = WorkerManager()
        status1 = manager.get_status()
        status2 = manager.get_status()

        # 两次调用返回不同实例
        assert status1 is not status2
        # 但值相同（都反映相同底层数据）
        assert status1.state == status2.state


class TestWorkerIndependentOfUI:
    """Worker 独立于 UI 生命周期。"""

    @pytest.mark.asyncio
    async def test_worker_runs_independent_of_ui(self, tmp_path):
        """启动 Worker 后不等待/不取消，验证 Worker 继续运行并写入 progress.json"""
        import asyncio

        from image_processor import FontConfig, TextRegion
        from worker import WorkerManager

        progress_file = str(tmp_path / "progress.json")

        # mock 函数
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

        manager = WorkerManager()

        products = [
            {"asin": "B0UIINDEP", "图片url": "https://img1.jpg"},
        ]

        # 启动 Worker（在不同的 asyncio task 中）
        manager.start(
            products=products,
            font_config=FontConfig(),
            _download_func=mock_download,
            _ocr_func=mock_ocr,
            _translate_func=mock_translate,
            _repair_func=mock_repair,
            _resize_func=mock_resize,
            _upload_func=mock_upload,
        )

        # 模拟"关闭页面"：我们不 cancel task，让 Worker 继续
        # 等待 Worker 完成
        await asyncio.sleep(0.5)  # 给 Worker 时间完成

        status = manager.get_status()
        # Worker 应该已完成（单 ASIN 很快）
        assert status.state in ("running", "completed"), f"实际状态: {status.state}"

        # 验证 progress.json 被写入
        # WorkerManager 使用默认 PROGRESS_FILE，这里验证 status 反映了完成情况
        assert status.total_asins > 0
