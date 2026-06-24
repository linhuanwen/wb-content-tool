"""
工作流引擎 — 多阶段流水线编排 & 后台执行。

提供：
    WorkflowStage   — 阶段枚举
    StageStatus     — 阶段状态枚举
    WorkflowRunner  — 后台编排器（daemon 线程 + 进度文件轮询）

复用现有模块的所有 public 函数，不做任何修改。
阶段顺序: CRAWL → PHASE1 → PHASE2 → IMAGE_TRANSLATION → IMAGE_CARD_DESIGN
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime as _dt


# ══════════════════════════════════════════════════════════════════════
# 类型定义
# ══════════════════════════════════════════════════════════════════════

class WorkflowStage(Enum):
    """工作流阶段枚举。"""
    CRAWL = "crawl"
    PHASE1_EXTRACTION = "phase1"
    PHASE2_GENERATION = "phase2"
    IMAGE_TRANSLATION = "image_translate"
    IMAGE_CARD_DESIGN = "card_design"


class StageStatus(Enum):
    """阶段状态枚举。"""
    NOT_STARTED = "not_started"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"


# 阶段顺序（用于自动推进）
PIPELINE_ORDER: list[WorkflowStage] = [
    WorkflowStage.CRAWL,
    WorkflowStage.PHASE1_EXTRACTION,
    WorkflowStage.PHASE2_GENERATION,
    WorkflowStage.IMAGE_TRANSLATION,
    WorkflowStage.IMAGE_CARD_DESIGN,
]

# 阶段显示名称
STAGE_LABELS: dict[WorkflowStage, str] = {
    WorkflowStage.CRAWL: "🕷️ 爬虫采集",
    WorkflowStage.PHASE1_EXTRACTION: "🔍 信息萃取 Phase1",
    WorkflowStage.PHASE2_GENERATION: "✍️ 文案生成 Phase2",
    WorkflowStage.IMAGE_TRANSLATION: "🖼️ 图片翻译",
    WorkflowStage.IMAGE_CARD_DESIGN: "🎨 产品卡片设计",
}

# 该阶段需要哪些 API 配置
STAGE_API_REQUIREMENTS: dict[WorkflowStage, list[str]] = {
    WorkflowStage.CRAWL: [],  # 爬虫不需要 AI API
    WorkflowStage.PHASE1_EXTRACTION: ["phase1_api_key"],
    WorkflowStage.PHASE2_GENERATION: ["phase2_or_phase1_api_key"],
    WorkflowStage.IMAGE_TRANSLATION: ["image_gen_or_phase1_api_key"],
    WorkflowStage.IMAGE_CARD_DESIGN: ["image_gen_api_key"],
}


@dataclass
class StageResult:
    """单个阶段的执行结果。"""
    status: str = "not_started"      # StageStatus value
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    summary: str = ""                # 如 "5/5 ASINs succeeded"
    download_label: str = ""         # 下载按钮标签
    download_filename: str = ""      # 下载文件名
    download_temp_path: str = ""     # 临时文件路径（含下载数据）


# ══════════════════════════════════════════════════════════════════════
# 进度文件读写（原子写入）
# ══════════════════════════════════════════════════════════════════════

def _atomic_write_json(filepath: str, data: dict) -> None:
    """原子写入 JSON 文件（先写 tmp 再 os.replace）。"""
    tmp_path = filepath + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    os.replace(tmp_path, filepath)


def read_workflow_progress(progress_file: str) -> dict | None:
    """安全读取工作流进度文件。

    Returns:
        进度字典，文件不存在或损坏时返回 None。
    """
    try:
        if not os.path.isfile(progress_file):
            return None
        with open(progress_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _build_initial_progress(
    asins: list[str],
    enabled_stages: list[str],
    image_translator_mode: str,
) -> dict:
    """构建初始工作流进度字典。"""
    stages = {}
    for stage in PIPELINE_ORDER:
        stages[stage.value] = {
            "status": "not_started",
            "started_at": "",
            "finished_at": "",
            "error": "",
            "summary": "",
            "download_label": "",
            "download_filename": "",
            "download_temp_path": "",
        }

    return {
        "overall_state": "idle",
        "asins": asins,
        "enabled_stages": enabled_stages,
        "image_translator_mode": image_translator_mode,
        "current_stage": "",
        "started_at": "",
        "finished_at": "",
        "stages": stages,
        "crawl_products_json_path": "",   # 爬虫产出的产品列表临时文件
    }


# ══════════════════════════════════════════════════════════════════════
# 后台执行入口
# ══════════════════════════════════════════════════════════════════════

def run_workflow_background(
    *,
    asins: list[str],
    enabled_stages: list[str],
    image_translator_mode: str = "ai",
    db_path: str = "products.db",
    html_dir: str = "html",
    progress_file: str = "workflow_progress.json",
    crawler_config: dict | None = None,
    api_config: dict | None = None,
    font_config: dict | None = None,
    custom_prompt: str = "",
) -> None:
    """在后台 daemon 线程中启动工作流。

    UI 线程通过 read_workflow_progress(progress_file) 轮询进度。

    Args:
        asins: ASIN 列表。
        enabled_stages: 启用的阶段列表（WorkflowStage value 字符串）。
        image_translator_mode: 图片翻译模式 "traditional" 或 "ai"。
        db_path: SQLite 数据库路径。
        html_dir: HTML 存档目录。
        progress_file: 工作流进度文件路径。
        crawler_config: 爬虫配置 dict（headless, delay_min, delay_max）。
        api_config: API 配置 dict（包含所有 session_state 中的 API 设置）。
        font_config: 字体配置 dict（font_name, auto_size, manual_size）。
        custom_prompt: 卡片设计自定义提示词。
    """
    # 1. 写入初始进度
    initial = _build_initial_progress(asins, enabled_stages, image_translator_mode)
    _atomic_write_json(progress_file, initial)

    # 2. 捕获配置快照（避免 daemon 线程读全局 settings）
    crawler_cfg = dict(crawler_config or {})
    api_cfg = dict(api_config or {})
    font_cfg = dict(font_config or {})

    def _run_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _run():
            runner = WorkflowRunner(
                asins=asins,
                enabled_stages=enabled_stages,
                image_translator_mode=image_translator_mode,
                db_path=db_path,
                html_dir=html_dir,
                progress_file=progress_file,
                crawler_config=crawler_cfg,
                api_config=api_cfg,
                font_config=font_cfg,
                custom_prompt=custom_prompt,
            )
            await runner.execute()

        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()


# ══════════════════════════════════════════════════════════════════════
# 工作流编排器
# ══════════════════════════════════════════════════════════════════════

class WorkflowRunner:
    """工作流编排器 — 按顺序执行已启用的阶段。"""

    def __init__(
        self,
        *,
        asins: list[str],
        enabled_stages: list[str],
        image_translator_mode: str,
        db_path: str,
        html_dir: str,
        progress_file: str,
        crawler_config: dict,
        api_config: dict,
        font_config: dict,
        custom_prompt: str,
    ):
        self.asins = asins
        self.enabled_stages = enabled_stages
        self.image_translator_mode = image_translator_mode
        self.db_path = db_path
        self.html_dir = html_dir
        self.progress_file = progress_file
        self.crawler_config = crawler_config
        self.api_config = api_config
        self.font_config = font_config
        self.custom_prompt = custom_prompt

    # ── 进度读写辅助 ──

    def _read_progress(self) -> dict:
        return read_workflow_progress(self.progress_file) or {}

    def _write_progress(self, data: dict) -> None:
        _atomic_write_json(self.progress_file, data)

    def _update_stage(self, stage: WorkflowStage, updates: dict) -> dict:
        """原子更新单个阶段的状态，返回最新进度。"""
        progress = self._read_progress()
        stage_key = stage.value
        if "stages" not in progress:
            progress["stages"] = {}
        if stage_key not in progress["stages"]:
            progress["stages"][stage_key] = {}
        progress["stages"][stage_key].update(updates)
        progress["current_stage"] = stage.value
        self._write_progress(progress)
        return progress

    # ── 主执行循环 ──

    async def execute(self) -> None:
        """按顺序执行所有已启用的阶段。"""
        progress = self._read_progress()
        progress["overall_state"] = "running"
        progress["started_at"] = _dt.now().isoformat()
        self._write_progress(progress)

        # 按 PIPELINE_ORDER 顺序执行
        for stage in PIPELINE_ORDER:
            if stage.value not in self.enabled_stages:
                # 未启用的阶段标记为 skipped
                self._update_stage(stage, {
                    "status": StageStatus.SKIPPED.value,
                    "summary": "未启用",
                })
                continue

            # 检查前一个阶段是否失败（如果是，跳过后续阶段）
            if self._prev_stage_failed(stage):
                self._update_stage(stage, {
                    "status": StageStatus.SKIPPED.value,
                    "summary": "前一阶段失败，已跳过",
                })
                continue

            # 执行阶段
            await self._execute_stage(stage)

        # 标记整体完成
        progress = self._read_progress()
        has_error = any(
            s.get("status") == StageStatus.ERROR.value
            for s in progress.get("stages", {}).values()
        )
        progress["overall_state"] = "completed" if not has_error else "error"
        progress["finished_at"] = _dt.now().isoformat()
        self._write_progress(progress)

    def _prev_stage_failed(self, stage: WorkflowStage) -> bool:
        """检查当前阶段之前是否有已启用阶段失败。"""
        progress = self._read_progress()
        stages = progress.get("stages", {})
        stage_idx = PIPELINE_ORDER.index(stage)

        for i, prev_stage in enumerate(PIPELINE_ORDER):
            if i >= stage_idx:
                break
            if prev_stage.value not in self.enabled_stages:
                continue
            prev_data = stages.get(prev_stage.value, {})
            if prev_data.get("status") == StageStatus.ERROR.value:
                return True
        return False

    async def _execute_stage(self, stage: WorkflowStage) -> None:
        """执行单个阶段，捕获异常并写入进度。"""
        now = _dt.now().isoformat()
        self._update_stage(stage, {
            "status": StageStatus.RUNNING.value,
            "started_at": now,
            "error": "",
        })

        try:
            if stage == WorkflowStage.CRAWL:
                await self._run_crawl_stage()
            elif stage == WorkflowStage.PHASE1_EXTRACTION:
                await self._run_phase1_stage()
            elif stage == WorkflowStage.PHASE2_GENERATION:
                await self._run_phase2_stage()
            elif stage == WorkflowStage.IMAGE_TRANSLATION:
                await self._run_image_translation_stage()
            elif stage == WorkflowStage.IMAGE_CARD_DESIGN:
                await self._run_card_design_stage()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._update_stage(stage, {
                "status": StageStatus.ERROR.value,
                "finished_at": _dt.now().isoformat(),
                "error": f"{e}\n\n{tb[-2000:]}",  # 截断 traceback
            })

    # ── 各阶段实现 ──

    async def _run_crawl_stage(self) -> None:
        """执行爬虫采集阶段。"""
        from crawler_ui import execute_crawl, generate_download

        stage = WorkflowStage.CRAWL

        # 同步爬虫配置到 settings
        self._sync_crawler_settings()

        headless = self.crawler_config.get("headless", True)
        delay_min = self.crawler_config.get("delay_min", 3.0)
        delay_max = self.crawler_config.get("delay_max", 8.0)

        # 进度回调：更新当前 ASIN
        last_asin = [""]

        def on_progress(current: int, total: int, asin: str, status: str):
            last_asin[0] = asin
            self._update_stage(stage, {
                "summary": f"采集中 {current}/{total}: {asin} {'✅' if status == 'ok' else '❌'}",
            })

        # 执行采集
        result = await execute_crawl(
            self.asins,
            headless=headless,
            delay_min=delay_min,
            delay_max=delay_max,
            progress_callback=on_progress,
        )

        success_count = len(result.products)
        fail_count = len(result.failed_asins)

        # 保存产品列表到临时 JSON 文件（供后续阶段使用）
        products_json_path = os.path.join(
            tempfile.gettempdir(),
            f"workflow_crawl_products_{_dt.now().strftime('%Y%m%d_%H%M%S')}.json",
        )
        _atomic_write_json(products_json_path, {
            "products": result.products,
            "failed_asins": result.failed_asins,
        })

        # 生成下载
        download_bytes = generate_download(result.products)
        download_path = self._save_temp_download(
            download_bytes, "爬虫表格（处理前）.xlsx"
        )

        summary = f"成功 {success_count}/{len(self.asins)}"
        if fail_count > 0:
            summary += f"，失败 {fail_count}: {', '.join(result.failed_asins[:5])}"
            if len(result.failed_asins) > 5:
                summary += f" ...等{len(result.failed_asins)}个"

        # 更新进度（同时保存 products_json_path 到顶层）
        progress = self._read_progress()
        progress["crawl_products_json_path"] = products_json_path
        self._write_progress(progress)

        self._update_stage(stage, {
            "status": StageStatus.DONE.value,
            "finished_at": _dt.now().isoformat(),
            "summary": summary,
            "download_label": "📥 下载采集表格 (4列)",
            "download_filename": "爬虫表格（处理前）.xlsx",
            "download_temp_path": download_path,
        })

    async def _run_phase1_stage(self) -> None:
        """执行 Phase 1 信息萃取阶段。"""
        from translator_ui import execute_phase1_extraction

        stage = WorkflowStage.PHASE1_EXTRACTION

        # 同步 Phase 1 API 配置
        self._sync_phase1_settings()

        # 检查 HTML 目录
        html_dir = self.html_dir
        if not os.path.isdir(html_dir):
            raise RuntimeError(
                f"HTML 存档目录不存在: {html_dir}。请先运行爬虫采集。"
            )

        missing_html = [
            a for a in self.asins
            if not os.path.isfile(os.path.join(html_dir, f"{a}.html"))
        ]
        if missing_html:
            raise RuntimeError(
                f"以下 {len(missing_html)} 个 ASIN 缺少 HTML 存档，"
                f"请先运行爬虫采集: {', '.join(missing_html[:5])}"
                + ("..." if len(missing_html) > 5 else "")
            )

        # 进度回调
        total = len(self.asins)
        last_cur = [0]

        def on_progress(current: int, total_count: int):
            last_cur[0] = current
            self._update_stage(stage, {
                "summary": f"萃取中 {current}/{total_count}",
            })

        # 执行
        results = execute_phase1_extraction(
            asins=self.asins,
            html_dir=html_dir,
            db_path=self.db_path,
            progress_callback=on_progress,
        )

        success_count = len([r for r in results if "error" not in r])
        fail_count = len([r for r in results if "error" in r])

        summary = f"成功 {success_count}/{len(self.asins)}"
        if fail_count > 0:
            failed_asins = [r["asin"] for r in results if "error" in r]
            summary += f"，失败 {fail_count}: {', '.join(failed_asins[:5])}"

        self._update_stage(stage, {
            "status": StageStatus.DONE.value,
            "finished_at": _dt.now().isoformat(),
            "summary": summary,
        })

    async def _run_phase2_stage(self) -> None:
        """执行 Phase 2 文案生成阶段。"""
        from translator_ui import execute_phase2_generation, generate_phase2_download

        stage = WorkflowStage.PHASE2_GENERATION

        # 同步 Phase 2 API 配置
        self._sync_phase2_settings()

        total = len(self.asins)

        def on_progress(current: int, total_count: int):
            self._update_stage(stage, {
                "summary": f"生成中 {current}/{total_count}",
            })

        results = execute_phase2_generation(
            asins=self.asins,
            db_path=self.db_path,
            progress_callback=on_progress,
        )

        success_count = len([r for r in results if "error" not in r])
        fail_count = len([r for r in results if "error" in r])

        # 生成下载
        success_asins = [r["asin"] for r in results if "error" not in r]
        download_path = ""
        if success_asins:
            try:
                download_bytes = generate_phase2_download(self.db_path, success_asins)
                download_path = self._save_temp_download(
                    download_bytes, "爬虫表格（处理后）.xlsx"
                )
            except Exception:
                pass

        summary = f"成功 {success_count}/{total}"
        if fail_count > 0:
            failed_asins = [r["asin"] for r in results if "error" in r]
            summary += f"，失败 {fail_count}: {', '.join(failed_asins[:5])}"

        self._update_stage(stage, {
            "status": StageStatus.DONE.value,
            "finished_at": _dt.now().isoformat(),
            "summary": summary,
            "download_label": "📥 下载翻译表格 (12列)" if download_path else "",
            "download_filename": "爬虫表格（处理后）.xlsx" if download_path else "",
            "download_temp_path": download_path,
        })

    async def _run_image_translation_stage(self) -> None:
        """执行图片翻译阶段（传统或 AI 管线）。"""
        stage = WorkflowStage.IMAGE_TRANSLATION

        # 构建产品列表
        products = self._get_products_for_image_stage()
        if not products:
            raise RuntimeError("没有可处理的产品数据。请先运行爬虫采集或上传包含图片URL的Excel。")

        # 从数据库丰富产品上下文
        products = self._enrich_products_from_db(products)

        if self.image_translator_mode == "traditional":
            await self._run_traditional_image_translation(products, stage)
        else:
            await self._run_ai_image_translation(products, stage)

    async def _run_traditional_image_translation(
        self, products: list[dict], stage: WorkflowStage
    ) -> None:
        """传统管线图片翻译（OCR + 翻译 + 覆写）。"""
        from worker import start_background_translation, read_progress
        from image_processor import FontConfig

        # 同步 API 配置
        self._sync_phase1_settings()

        # 字体配置
        fc = FontConfig(
            font_name=self.font_config.get("font_name", "Roboto-Regular.ttf"),
            auto_size=self.font_config.get("auto_size", True),
            manual_size=self.font_config.get("manual_size", 24),
        )

        sub_progress_file = "workflow_image_traditional_progress.json"

        # 启动后台翻译
        start_background_translation(
            products=products,
            font_config=fc,
            progress_file=sub_progress_file,
        )

        # 轮询子进度
        await self._poll_sub_progress(
            sub_progress_file=sub_progress_file,
            stage=stage,
            stage_label="传统管线图片翻译",
        )

    async def _run_ai_image_translation(
        self, products: list[dict], stage: WorkflowStage
    ) -> None:
        """AI 管线图片翻译（Gemini / 中转站）。"""
        from worker import start_background_card_generation, read_progress

        # 同步 AI 图片 API 配置
        self._sync_image_gen_settings()

        sub_progress_file = "workflow_image_ai_progress.json"

        start_background_card_generation(
            products=products,
            mode="translate",
            progress_file=sub_progress_file,
        )

        await self._poll_sub_progress(
            sub_progress_file=sub_progress_file,
            stage=stage,
            stage_label="AI 管线图片翻译",
        )

    async def _run_card_design_stage(self) -> None:
        """执行产品卡片设计阶段。"""
        from worker import start_background_card_generation

        stage = WorkflowStage.IMAGE_CARD_DESIGN

        products = self._get_products_for_image_stage()
        if not products:
            raise RuntimeError("没有可处理的产品数据。")

        products = self._enrich_products_from_db(products)

        self._sync_image_gen_settings()

        sub_progress_file = "workflow_card_design_progress.json"

        start_background_card_generation(
            products=products,
            mode="card_design",
            custom_prompt=self.custom_prompt,
            progress_file=sub_progress_file,
        )

        await self._poll_sub_progress(
            sub_progress_file=sub_progress_file,
            stage=stage,
            stage_label="产品卡片设计",
        )

    # ── 子进度轮询 ──

    async def _poll_sub_progress(
        self,
        sub_progress_file: str,
        stage: WorkflowStage,
        stage_label: str,
        timeout_seconds: int = 3600,  # 默认 1 小时超时
    ) -> None:
        """轮询子进度文件直到完成或超时。"""
        from worker import read_progress as read_sub_progress

        elapsed = 0
        poll_interval = 5  # 秒

        while elapsed < timeout_seconds:
            sub = read_sub_progress(sub_progress_file)
            if sub is None:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval
                continue

            state = sub.get("state", "running")
            total = sub.get("total_images", 0)
            processed = sub.get("processed_images", 0)
            current_asin = sub.get("current_asin", "")

            self._update_stage(stage, {
                "summary": f"{stage_label}: {processed}/{total} 图片 | {current_asin}",
            })

            if state == "completed":
                success = sub.get("success_count", 0)
                errors = sub.get("error_count", 0)
                skipped = sub.get("skipped_count", 0)
                videos = sub.get("video_count", 0)

                summary = f"成功 {success}"
                parts = []
                if skipped > 0:
                    parts.append(f"跳过 {skipped}")
                if videos > 0:
                    parts.append(f"视频 {videos}")
                if errors > 0:
                    parts.append(f"失败 {errors}")
                if parts:
                    summary += " | " + " | ".join(parts)
                summary += f"（共 {total} 张）"

                self._update_stage(stage, {
                    "status": StageStatus.DONE.value,
                    "finished_at": _dt.now().isoformat(),
                    "summary": summary,
                })
                return

            if state == "error":
                raise RuntimeError(f"{stage_label} 出错: {sub.get('error', '未知错误')}")

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise TimeoutError(f"{stage_label} 超时（{timeout_seconds // 60} 分钟）")

    # ── 辅助方法 ──

    def _get_products_for_image_stage(self) -> list[dict]:
        """获取图片处理阶段所需的产品列表。

        优先级：
        1. 从爬虫产出 JSON 读取（如果在本工作流中执行了爬虫）
        2. 否则返回空列表（调用方需自行处理）
        """
        progress = self._read_progress()
        products_json_path = progress.get("crawl_products_json_path", "")

        if products_json_path and os.path.isfile(products_json_path):
            data = read_workflow_progress(products_json_path)
            if data:
                return data.get("products", [])

        return []

    def _enrich_products_from_db(self, products: list[dict]) -> list[dict]:
        """从数据库丰富产品上下文。"""
        from image_translator_ui import enrich_product_context_from_db
        return enrich_product_context_from_db(
            list(products),  # 不修改原列表
            self.db_path,
        )

    @staticmethod
    def _save_temp_download(data: bytes, filename: str) -> str:
        """将下载数据保存到临时文件，返回路径。"""
        suffix = os.path.splitext(filename)[1]
        tmp = tempfile.NamedTemporaryFile(
            suffix=suffix, delete=False, prefix="workflow_download_"
        )
        tmp.write(data)
        tmp.close()
        return tmp.name

    # ── settings 同步 ──

    def _sync_crawler_settings(self) -> None:
        """同步爬虫配置到 settings。"""
        from config import settings
        settings.crawler_mode = self.crawler_config.get(
            "crawler_mode", settings.crawler_mode
        )
        if settings.crawler_mode == "scraperapi":
            settings.scraperapi_key = self.crawler_config.get(
                "scraperapi_key", settings.scraperapi_key
            )

    def _sync_phase1_settings(self) -> None:
        """同步 Phase 1 API 配置到 settings。"""
        from config import settings
        settings.phase1_api_provider = self.api_config.get(
            "p1_provider", settings.phase1_api_provider
        )
        settings.phase1_api_key = self.api_config.get(
            "p1_api_key", settings.phase1_api_key
        )
        settings.phase1_api_base_url = self.api_config.get(
            "p1_base_url", settings.phase1_api_base_url
        )
        settings.phase1_model = self.api_config.get(
            "p1_model", settings.phase1_model
        )

    def _sync_phase2_settings(self) -> None:
        """同步 Phase 2 API 配置到 settings。"""
        from config import settings
        # 如果 Phase 2 独立配置则用它，否则 fallback 到 Phase 1
        p2_key = self.api_config.get("p2_api_key", "")
        if p2_key:
            settings.phase2_api_provider = self.api_config.get(
                "p2_provider", settings.phase2_api_provider
            )
            settings.phase2_api_key = p2_key
            settings.phase2_api_base_url = self.api_config.get(
                "p2_base_url", settings.phase2_api_base_url
            )
            settings.phase2_model = self.api_config.get(
                "p2_model", settings.phase2_model
            )
        else:
            # Fallback 到 Phase 1
            self._sync_phase1_settings()
            settings.phase2_api_provider = settings.phase1_api_provider
            settings.phase2_api_key = settings.phase1_api_key
            settings.phase2_api_base_url = settings.phase1_api_base_url
            settings.phase2_model = settings.phase1_model

    def _sync_image_gen_settings(self) -> None:
        """同步 AI 图片 API 配置到 settings。"""
        from config import settings
        provider = self.api_config.get("image_gen_provider", "")
        if provider:
            settings.image_gen_provider = provider
        settings.gemini_api_key = self.api_config.get(
            "gemini_api_key", settings.gemini_api_key
        )
        settings.gemini_model = self.api_config.get(
            "gemini_model", settings.gemini_model
        )
        settings.gemini_proxy = self.api_config.get(
            "gemini_proxy", getattr(settings, "gemini_proxy", "")
        )
        settings.image_gen_api_key = self.api_config.get(
            "image_gen_api_key", getattr(settings, "image_gen_api_key", "")
        )
        settings.image_gen_base_url = self.api_config.get(
            "image_gen_base_url", getattr(settings, "image_gen_base_url", "")
        )
        settings.image_gen_model = self.api_config.get(
            "image_gen_model", getattr(settings, "image_gen_model", "")
        )


# ══════════════════════════════════════════════════════════════════════
# API 配置校验
# ══════════════════════════════════════════════════════════════════════

def validate_workflow_config(
    enabled_stages: list[str],
    api_config: dict,
) -> list[str]:
    """校验工作流所需的 API 配置是否齐全。

    Returns:
        缺失配置的警告信息列表（空列表表示一切就绪）。
    """
    warnings: list[str] = []

    p1_key = api_config.get("p1_api_key", "")
    p2_key = api_config.get("p2_api_key", "")
    gemini_key = api_config.get("gemini_api_key", "")
    image_gen_key = api_config.get("image_gen_api_key", "")
    image_gen_base = api_config.get("image_gen_base_url", "")

    has_phase1 = bool(p1_key)
    has_phase2 = bool(p2_key or p1_key)  # Phase 2 can fallback to Phase 1
    has_image_gen = bool(gemini_key or (image_gen_key and image_gen_base))

    if WorkflowStage.PHASE1_EXTRACTION.value in enabled_stages and not has_phase1:
        warnings.append("⚠️ Phase 1 信息萃取需要配置 API Key（侧边栏「🔍 Phase 1 API」）")

    if WorkflowStage.PHASE2_GENERATION.value in enabled_stages and not has_phase2:
        warnings.append("⚠️ Phase 2 文案生成需要配置 API Key（侧边栏「✍️ Phase 2 API」）")

    image_stages = [
        WorkflowStage.IMAGE_TRANSLATION.value,
        WorkflowStage.IMAGE_CARD_DESIGN.value,
    ]
    if any(s in enabled_stages for s in image_stages) and not has_image_gen:
        warnings.append("⚠️ 图片处理需要配置 AI 图片 API（侧边栏「🎨 AI 图片 API」）")

    return warnings
