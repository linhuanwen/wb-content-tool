"""
测试 workflow_engine.py — 工作流状态机 & 后台编排器。

测试行为（非实现）：
- 工作流进度文件读写（原子性）
- 阶段状态转换
- API 配置校验
- 阶段排序 & 跳过逻辑
- 错误传播 & 重试准备
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ══════════════════════════════════════════════════════════════════════
# 进度文件读写
# ══════════════════════════════════════════════════════════════════════

class TestProgressFileIO:
    """测试进度文件的原子读写。"""

    def test_read_nonexistent_file(self):
        from workflow_engine import read_workflow_progress
        result = read_workflow_progress("/nonexistent/path/workflow_progress.json")
        assert result is None

    def test_read_corrupted_file(self):
        from workflow_engine import read_workflow_progress
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write("not valid json {{{")
        result = read_workflow_progress(path)
        os.unlink(path)
        assert result is None

    def test_atomic_write_and_read(self):
        from workflow_engine import _atomic_write_json, read_workflow_progress
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)

        data = {"key": "value", "nested": {"a": 1}}
        _atomic_write_json(path, data)

        result = read_workflow_progress(path)
        os.unlink(path)

        assert result is not None
        assert result["key"] == "value"
        assert result["nested"]["a"] == 1

    def test_atomic_write_no_partial_read(self):
        """验证原子写入不会产生损坏文件（tmp 文件已替换）。"""
        from workflow_engine import _atomic_write_json
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)

        _atomic_write_json(path, {"hello": "world"})

        # tmp 文件不应存在（已被 os.replace）
        tmp_path = path + ".tmp"
        assert not os.path.isfile(tmp_path)

        # 原文件应正常可读
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        os.unlink(path)
        assert data["hello"] == "world"


# ══════════════════════════════════════════════════════════════════════
# 初始进度构建
# ══════════════════════════════════════════════════════════════════════

class TestBuildInitialProgress:
    """测试初始进度字典构建。"""

    def test_build_basic(self):
        from workflow_engine import _build_initial_progress, PIPELINE_ORDER

        asins = ["B0ABC", "B0DEF"]
        enabled = ["crawl", "phase1", "phase2"]
        mode = "ai"

        progress = _build_initial_progress(asins, enabled, mode)

        assert progress["overall_state"] == "idle"
        assert progress["asins"] == asins
        assert progress["enabled_stages"] == enabled
        assert progress["image_translator_mode"] == mode
        assert "stages" in progress

        # 所有阶段都应存在，且初始状态为 not_started
        for stage in PIPELINE_ORDER:
            assert stage.value in progress["stages"]
            assert progress["stages"][stage.value]["status"] == "not_started"

    def test_build_empty_asins(self):
        from workflow_engine import _build_initial_progress
        progress = _build_initial_progress([], ["crawl"], "traditional")
        assert progress["asins"] == []
        assert progress["enabled_stages"] == ["crawl"]


# ══════════════════════════════════════════════════════════════════════
# API 配置校验
# ══════════════════════════════════════════════════════════════════════

class TestValidateWorkflowConfig:
    """测试 API 配置校验逻辑。"""

    def test_all_configured_no_warnings(self):
        from workflow_engine import validate_workflow_config

        api_config = {
            "p1_api_key": "sk-xxx",
            "p2_api_key": "sk-yyy",
            "gemini_api_key": "gemini-xxx",
            "image_gen_api_key": "",
            "image_gen_base_url": "",
        }
        enabled = ["crawl", "phase1", "phase2", "image_translate"]

        warnings = validate_workflow_config(enabled, api_config)
        assert len(warnings) == 0

    def test_missing_phase1_key(self):
        from workflow_engine import validate_workflow_config

        api_config = {"p1_api_key": "", "p2_api_key": "", "gemini_api_key": ""}
        enabled = ["phase1"]

        warnings = validate_workflow_config(enabled, api_config)
        assert len(warnings) == 1
        assert "Phase 1" in warnings[0]

    def test_phase2_fallback_to_phase1(self):
        from workflow_engine import validate_workflow_config

        # Phase 2 有独立 key 或者 Phase 1 有 key 都可以
        api_config = {
            "p1_api_key": "sk-xxx",
            "p2_api_key": "",
            "gemini_api_key": "",
        }
        enabled = ["phase2"]

        warnings = validate_workflow_config(enabled, api_config)
        assert len(warnings) == 0  # Phase 2 可以 fallback 到 Phase 1

    def test_missing_image_gen_key(self):
        from workflow_engine import validate_workflow_config

        api_config = {
            "p1_api_key": "sk-xxx",
            "gemini_api_key": "",
            "image_gen_api_key": "",
            "image_gen_base_url": "",
        }
        enabled = ["image_translate"]

        warnings = validate_workflow_config(enabled, api_config)
        assert len(warnings) == 1
        assert "图片" in warnings[0]

    def test_only_crawl_needs_no_api(self):
        from workflow_engine import validate_workflow_config

        api_config = {}
        enabled = ["crawl"]

        warnings = validate_workflow_config(enabled, api_config)
        assert len(warnings) == 0


# ══════════════════════════════════════════════════════════════════════
# 阶段排序 & 枚举
# ══════════════════════════════════════════════════════════════════════

class TestWorkflowStageOrder:
    """测试阶段顺序和常量。"""

    def test_pipeline_order(self):
        from workflow_engine import PIPELINE_ORDER, WorkflowStage

        assert PIPELINE_ORDER[0] == WorkflowStage.CRAWL
        assert PIPELINE_ORDER[1] == WorkflowStage.PHASE1_EXTRACTION
        assert PIPELINE_ORDER[2] == WorkflowStage.PHASE2_GENERATION
        assert PIPELINE_ORDER[3] == WorkflowStage.IMAGE_TRANSLATION
        assert PIPELINE_ORDER[4] == WorkflowStage.IMAGE_CARD_DESIGN

    def test_stage_labels_all_present(self):
        from workflow_engine import STAGE_LABELS, PIPELINE_ORDER

        for stage in PIPELINE_ORDER:
            assert stage in STAGE_LABELS
            assert isinstance(STAGE_LABELS[stage], str)
            assert len(STAGE_LABELS[stage]) > 0


# ══════════════════════════════════════════════════════════════════════
# WorkflowRunner 阶段跳过 & 错误传播
# ══════════════════════════════════════════════════════════════════════

class TestWorkflowRunnerStageSkip:
    """测试阶段跳过和错误传播逻辑。"""

    def _make_runner(self, enabled_stages, progress_file=None):
        from workflow_engine import WorkflowRunner
        return WorkflowRunner(
            asins=["B0TEST"],
            enabled_stages=enabled_stages,
            image_translator_mode="ai",
            db_path=":memory:",
            html_dir="/nonexistent_html",
            progress_file=progress_file or "test_workflow_progress.json",
            crawler_config={"headless": True, "delay_min": 1.0, "delay_max": 2.0},
            api_config={"p1_api_key": "", "gemini_api_key": ""},
            font_config={"font_name": "Arial.ttf", "auto_size": True, "manual_size": 24},
            custom_prompt="",
        )

    def test_prev_stage_failed_detection(self):
        """测试前序阶段失败检测。"""
        from workflow_engine import (
            WorkflowStage, StageStatus, _atomic_write_json, read_workflow_progress,
            PIPELINE_ORDER,
        )

        fd, progress_file = tempfile.mkstemp(suffix=".json")
        os.close(fd)

        # 构建初始进度，手动设置 crawl 为 error
        from workflow_engine import _build_initial_progress
        progress = _build_initial_progress(
            ["B0TEST"],
            ["crawl", "phase1", "phase2"],
            "ai",
        )
        progress["stages"]["crawl"]["status"] = StageStatus.ERROR.value
        progress["stages"]["crawl"]["error"] = "test error"
        _atomic_write_json(progress_file, progress)

        runner = self._make_runner(
            ["crawl", "phase1", "phase2"],
            progress_file=progress_file,
        )

        # crawl 已失败，phase1 应该有失败的前序阶段
        assert runner._prev_stage_failed(WorkflowStage.PHASE1_EXTRACTION) is True
        # crawl 是第一个阶段，没有前序
        assert runner._prev_stage_failed(WorkflowStage.CRAWL) is False

        os.unlink(progress_file)

    def test_prev_stage_succeeded(self):
        """测试前序阶段成功时不应阻断。"""
        from workflow_engine import (
            WorkflowStage, StageStatus, _atomic_write_json,
        )

        fd, progress_file = tempfile.mkstemp(suffix=".json")
        os.close(fd)

        from workflow_engine import _build_initial_progress
        progress = _build_initial_progress(
            ["B0TEST"],
            ["crawl", "phase1", "phase2"],
            "ai",
        )
        progress["stages"]["crawl"]["status"] = StageStatus.DONE.value
        _atomic_write_json(progress_file, progress)

        runner = self._make_runner(
            ["crawl", "phase1", "phase2"],
            progress_file=progress_file,
        )

        assert runner._prev_stage_failed(WorkflowStage.PHASE1_EXTRACTION) is False

        os.unlink(progress_file)

    def test_disabled_stage_not_checked(self):
        """测试未启用阶段的前序检查：跳过未启用的，只检查已启用的前序。"""
        from workflow_engine import (
            WorkflowStage, StageStatus, _atomic_write_json,
        )

        fd, progress_file = tempfile.mkstemp(suffix=".json")
        os.close(fd)

        from workflow_engine import _build_initial_progress
        progress = _build_initial_progress(
            ["B0TEST"],
            ["crawl", "phase2"],  # phase1 未启用
            "ai",
        )
        # crawl done, phase1 not enabled (skipped), phase2 should proceed
        progress["stages"]["crawl"]["status"] = StageStatus.DONE.value
        _atomic_write_json(progress_file, progress)

        runner = self._make_runner(
            ["crawl", "phase2"],  # phase1 不在 enabled 列表中
            progress_file=progress_file,
        )

        # crawl 成功了，phase1 未启用（跳过），phase2 不应检测到失败
        assert runner._prev_stage_failed(WorkflowStage.PHASE2_GENERATION) is False

        os.unlink(progress_file)


# ══════════════════════════════════════════════════════════════════════
# 阶段状态更新
# ══════════════════════════════════════════════════════════════════════

class TestWorkflowRunnerUpdateStage:
    """测试原子阶段状态更新。"""

    def test_update_stage_basic(self):
        from workflow_engine import (
            WorkflowStage, StageStatus, _atomic_write_json, _build_initial_progress,
        )

        fd, progress_file = tempfile.mkstemp(suffix=".json")
        os.close(fd)

        progress = _build_initial_progress(
            ["B0TEST"], ["crawl", "phase1"], "traditional"
        )
        _atomic_write_json(progress_file, progress)

        from workflow_engine import WorkflowRunner
        runner = WorkflowRunner(
            asins=["B0TEST"],
            enabled_stages=["crawl", "phase1"],
            image_translator_mode="traditional",
            db_path=":memory:",
            html_dir="/nonexistent",
            progress_file=progress_file,
            crawler_config={},
            api_config={},
            font_config={},
            custom_prompt="",
        )

        updated = runner._update_stage(WorkflowStage.CRAWL, {
            "status": StageStatus.RUNNING.value,
            "started_at": "2024-01-01T00:00:00",
        })

        assert updated["stages"]["crawl"]["status"] == StageStatus.RUNNING.value
        assert updated["current_stage"] == WorkflowStage.CRAWL.value

        os.unlink(progress_file)


# ══════════════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════════════

class TestSaveTempDownload:
    def test_save_and_read(self):
        from workflow_engine import WorkflowRunner

        path = WorkflowRunner._save_temp_download(b"hello world", "test.xlsx")
        assert os.path.isfile(path)
        with open(path, "rb") as f:
            assert f.read() == b"hello world"

        # cleanup
        os.unlink(path)
