"""
后台 Worker 模块 — UI/Worker 分离架构的核心。

提供：
    _atomic_write_json — 原子写入 JSON 文件（先写 tmp 再 os.replace）
    WorkerStatus       — Worker 状态快照
    WorkerManager      — 后台 Worker 管理器（单例）

原子写入保证在网络断开、进程被杀等异常情况下 progress.json 不损坏。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

PROGRESS_FILE = "progress.json"


def _atomic_write_json(filepath: str, data: dict) -> None:
    """原子写入 JSON 文件。

    先写入临时文件，再通过 os.replace 原子替换到目标路径。
    保证在任何时刻中断（断电、kill）都不会产生损坏的 JSON 文件。

    Args:
        filepath: 目标文件路径（如 "progress.json"）。
        data: 要写入的字典数据。
    """
    tmp_path = filepath + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp_path, filepath)


@dataclass
class WorkerStatus:
    """Worker 当前状态的快照。

    Attributes:
        state: "idle" | "running" | "paused" | "completed" | "error"
        total_asins: 总 ASIN 数。
        completed_asins: 已完成 ASIN 数。
        total_images: 总图片数。
        processed_images: 已处理图片数。
        current_asin: 当前正在处理的 ASIN。
        started_at: 开始时间 ISO 字符串。
        updated_at: 最后更新时间 ISO 字符串。
        error: 错误信息（state=error 时非空）。
    """
    state: str = "idle"
    total_asins: int = 0
    completed_asins: int = 0
    total_images: int = 0
    processed_images: int = 0
    current_asin: str = ""
    started_at: str = ""
    updated_at: str = ""
    error: str = ""


class WorkerManager:
    """后台 Worker 管理器（单例）。

    UI/Worker 分离的核心桥梁：
    - start(): 启动后台 asyncio task（不依赖 Streamlit WebSocket）
    - get_status(): 读 progress.json 返回 WorkerStatus
    - pause()/resume(): 控制任务暂停/恢复
    - 线程安全：progress.json 原子写入（先写 tmp 再 os.replace）
    """

    _instance: WorkerManager | None = None

    @classmethod
    def reset(cls) -> None:
        """重置单例（仅用于测试）。"""
        if cls._instance is not None:
            cls._instance._initialized = False
        cls._instance = None

    def __new__(cls) -> WorkerManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._state = "idle"
        self._total_asins = 0
        self._completed_asins = 0
        self._total_images = 0
        self._processed_images = 0
        self._current_asin = ""
        self._started_at = ""
        self._error = ""
        self._task: asyncio.Task | None = None
        self._result: object = None  # BatchImageResult，完成后存储
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # 初始为未暂停状态
        self._lock = threading.Lock()

    def start(
        self,
        products: list[dict],
        font_config=None,
        *,
        _download_func: Callable | None = None,
        _ocr_func: Callable | None = None,
        _translate_func: Callable | None = None,
        _repair_func: Callable | None = None,
        _resize_func: Callable | None = None,
        _upload_func: Callable | None = None,
    ) -> None:
        """启动后台翻译任务。

        在独立的 asyncio task 中运行，不阻塞调用方。
        任务独立于 Streamlit WebSocket 生命周期。

        Args:
            products: 产品列表。
            font_config: 字体配置。
            _*_func: 测试注入用。
        """
        from datetime import datetime as _dt

        # 新运行前清除旧的 progress.json
        if os.path.isfile(PROGRESS_FILE):
            os.remove(PROGRESS_FILE)

        with self._lock:
            self._state = "running"
            self._total_asins = len(products)
            self._completed_asins = 0
            self._started_at = _dt.now().isoformat()
            self._error = ""
            self._pause_event.set()

        # 在后台启动 asyncio task
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 没有运行中的 event loop，创建一个新的
            loop = asyncio.new_event_loop()

        self._task = loop.create_task(
            self._run(
                products=products,
                font_config=font_config,
                _download_func=_download_func,
                _ocr_func=_ocr_func,
                _translate_func=_translate_func,
                _repair_func=_repair_func,
                _resize_func=_resize_func,
                _upload_func=_upload_func,
            )
        )

    async def _run(
        self,
        products: list[dict],
        font_config,
        *,
        _download_func=None,
        _ocr_func=None,
        _translate_func=None,
        _repair_func=None,
        _resize_func=None,
        _upload_func=None,
    ) -> None:
        """后台执行批量翻译（内部方法）。"""
        from image_translator import translate_batch

        try:
            result = await translate_batch(
                products=products,
                font_config=font_config,
                progress_callback=self._on_asin_complete,
                resume_from=PROGRESS_FILE,
                _download_func=_download_func,
                _ocr_func=_ocr_func,
                _translate_func=_translate_func,
                _repair_func=_repair_func,
                _resize_func=_resize_func,
                _upload_func=_upload_func,
            )
            with self._lock:
                self._state = "completed"
                self._completed_asins = result.completed_asins
                self._total_images = result.total_images
                self._processed_images = result.success_images
                self._result = result
        except Exception as e:
            with self._lock:
                self._state = "error"
                self._error = str(e)

    def _on_asin_complete(self, asin_result) -> None:
        """单个 ASIN 完成后的回调（由 translate_batch 调用）。"""
        with self._lock:
            self._completed_asins += 1
            self._current_asin = asin_result.asin
            self._total_images += len(asin_result.images)
            self._processed_images += asin_result.success_count

    def pause(self) -> None:
        """暂停后台 Worker。"""
        self._pause_event.clear()

    def resume(self) -> None:
        """恢复后台 Worker。"""
        self._pause_event.set()

    def get_status(self) -> WorkerStatus:
        """获取当前 Worker 状态快照。

        Returns:
            WorkerStatus 包含当前进度、状态等信息。
        """
        with self._lock:
            return WorkerStatus(
                state=self._state,
                total_asins=self._total_asins,
                completed_asins=self._completed_asins,
                total_images=self._total_images,
                processed_images=self._processed_images,
                current_asin=self._current_asin,
                started_at=self._started_at,
                error=self._error,
            )

    def get_result(self):
        """获取批量处理结果（仅在 state=completed 时有值）。

        Returns:
            BatchImageResult 或 None。
        """
        with self._lock:
            return self._result

    def run_sync(
        self,
        products: list[dict],
        font_config=None,
        *,
        _download_func=None,
        _ocr_func=None,
        _translate_func=None,
        _repair_func=None,
        _resize_func=None,
        _upload_func=None,
    ):
        """同步运行批量翻译（阻塞当前线程直到完成）。

        用于 Streamlit UI 等需要等待结果的场景。

        Returns:
            BatchImageResult。
        """
        import asyncio as _asyncio

        from datetime import datetime as _dt

        # 新运行前清除旧的 progress.json，避免上一轮的 completed_asins
        # 导致所有 ASIN 被跳过
        if os.path.isfile(PROGRESS_FILE):
            os.remove(PROGRESS_FILE)

        with self._lock:
            self._state = "running"
            self._total_asins = len(products)
            self._completed_asins = 0
            self._started_at = _dt.now().isoformat()
            self._error = ""
            self._result = None

        _asyncio.run(
            self._run(
                products=products,
                font_config=font_config,
                _download_func=_download_func,
                _ocr_func=_ocr_func,
                _translate_func=_translate_func,
                _repair_func=_repair_func,
                _resize_func=_resize_func,
                _upload_func=_upload_func,
            )
        )
        return self._result

    def run_card_generation_sync(
        self,
        products: list[dict],
        *,
        mode: str = "card_design",
        custom_prompt: str = "",
        _download_func=None,
        _generate_func=None,
        _resize_func=None,
        _upload_func=None,
    ):
        """同步运行批量卡片生成（阻塞当前线程直到完成）。

        用于 Streamlit UI 等需要等待结果的场景。
        与 run_sync 平行，但委托给 Gemini 卡片生成管线。

        Returns:
            BatchCardResult。
        """
        import asyncio as _asyncio

        from datetime import datetime as _dt
        from image_generator import generate_batch_cards

        CARD_PROGRESS_FILE = "card_progress.json"

        # 新运行前清除旧的 card_progress.json
        if os.path.isfile(CARD_PROGRESS_FILE):
            os.remove(CARD_PROGRESS_FILE)

        with self._lock:
            self._state = "running"
            self._total_asins = len(products)
            self._completed_asins = 0
            self._started_at = _dt.now().isoformat()
            self._error = ""
            self._result = None

        async def _run_cards():
            return await generate_batch_cards(
                products=products,
                progress_callback=self._on_card_generation_complete,
                resume_from=CARD_PROGRESS_FILE,
                mode=mode,
                custom_prompt=custom_prompt,
                _download_func=_download_func,
                _generate_func=_generate_func,
                _resize_func=_resize_func,
                _upload_func=_upload_func,
            )

        result = _asyncio.run(_run_cards())

        with self._lock:
            self._state = "completed"
            self._completed_asins = result.completed_asins
            self._total_images = result.total_cards
            self._processed_images = result.success_cards
            self._result = result

        return self._result

    def _on_card_generation_complete(self, asin_result) -> None:
        """单个 ASIN 卡片生成完成后的回调。"""
        with self._lock:
            self._completed_asins += 1
            self._current_asin = asin_result.asin
            self._total_images = getattr(self, '_total_images', 0) + len(asin_result.cards)
            self._processed_images = getattr(self, '_processed_images', 0) + asin_result.success_count


# ══════════════════════════════════════════════════════════════════════
# 后台线程执行 + 实时图片级进度追踪
# ══════════════════════════════════════════════════════════════════════

import re as _re


def _build_initial_image_progress(
    products: list[dict],
) -> dict:
    """根据产品列表构建初始进度数据，所有图片标记为 pending。

    用于在后台任务启动前写入进度文件，UI 轮询时可展示完整图片列表。
    """
    from datetime import datetime as _dt

    asin_results: dict[str, dict] = {}
    total_images = 0
    pending_asins: list[str] = []

    for p in products:
        asin = p.get("asin", "")
        if not asin:
            continue
        pending_asins.append(asin)

        urls = [u.strip() for u in _re.split(r'[;|]', p.get("图片url", "")) if u.strip()]
        images = []
        for idx, url in enumerate(urls):
            images.append({
                "index": idx,
                "original_url": url,
                "status": "pending",
                "r2_url": "",
                "error": "",
            })
            total_images += 1

        asin_results[asin] = {
            "asin": asin,
            "images": images,
        }

    return {
        "state": "running",
        "completed_asins": [],
        "pending_asins": pending_asins,
        "current_asin": "",
        "total_asins": len(products),
        "total_images": total_images,
        "processed_images": 0,
        "asin_results": asin_results,
        "started_at": _dt.now().isoformat(),
        "updated_at": _dt.now().isoformat(),
    }


def _serialize_asin_image_result(asin_result) -> dict:
    """将 AsinImageResult 序列化为字典（传统管线）。"""
    images = []
    for img in asin_result.images:
        images.append({
            "index": img.index,
            "original_url": img.original_url,
            "status": img.status,
            "r2_url": img.r2_url,
            "error": img.error,
        })
    return {
        "asin": asin_result.asin,
        "images": images,
    }


def _serialize_asin_card_result(asin_result) -> dict:
    """将 AsinCardResult 序列化为字典（AI 管线）。"""
    cards = []
    for card in asin_result.cards:
        cards.append({
            "index": card.index,
            "original_url": card.original_url,
            "status": card.status,
            "r2_url": card.r2_url,
            "error": card.error,
        })
    return {
        "asin": asin_result.asin,
        "images": cards,
    }


def _update_progress_with_asin(
    progress_file: str,
    asin_data: dict,
) -> None:
    """原子更新进度文件：将已完成 ASIN 的结果写入，重算计数。"""
    import json as _json
    from datetime import datetime as _dt

    try:
        if os.path.isfile(progress_file):
            with open(progress_file, "r", encoding="utf-8") as f:
                progress = _json.load(f)
        else:
            progress = {}
    except (_json.JSONDecodeError, OSError):
        progress = {}

    asin = asin_data["asin"]
    asin_results = progress.get("asin_results", {})
    asin_results[asin] = asin_data

    # 重算计数
    total_images = 0
    processed_images = 0
    for ar in asin_results.values():
        for img in ar.get("images", []):
            total_images += 1
            if img["status"] != "pending":
                processed_images += 1

    completed_asins = [
        a for a, ar in asin_results.items()
        if all(img["status"] != "pending" for img in ar.get("images", []))
    ]

    progress["state"] = "running"
    progress["asin_results"] = asin_results
    progress["completed_asins"] = completed_asins
    progress["current_asin"] = asin
    progress["total_images"] = total_images
    progress["processed_images"] = processed_images
    progress["updated_at"] = _dt.now().isoformat()

    _atomic_write_json(progress_file, progress)


def read_progress(progress_file: str) -> dict | None:
    """安全读取进度文件，返回字典或 None。"""
    import json as _json

    try:
        if not os.path.isfile(progress_file):
            return None
        with open(progress_file, "r", encoding="utf-8") as f:
            return _json.load(f)
    except (_json.JSONDecodeError, OSError):
        return None


def start_background_translation(
    products: list[dict],
    font_config=None,
    *,
    progress_file: str = "image_progress.json",
) -> None:
    """在后台 daemon 线程中启动传统管线图片翻译。

    主线程可通过 read_progress(progress_file) 轮询进度，
    进度文件中包含每张图片的实时状态（pending/ok/error/skipped/video）。
    """
    import asyncio as _asyncio
    from datetime import datetime as _dt

    from image_translator import translate_batch

    # 1. 写入初始进度（所有图片标记为 pending）
    initial = _build_initial_image_progress(products)
    _atomic_write_json(progress_file, initial)

    def _run_in_thread():
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)

        async def _run():
            def on_asin_complete(asin_result):
                data = _serialize_asin_image_result(asin_result)
                _update_progress_with_asin(progress_file, data)

            result = await translate_batch(
                products=products,
                font_config=font_config,
                progress_callback=on_asin_complete,
                resume_from=progress_file,
            )

            # 写入完成状态
            final = read_progress(progress_file) or {}
            final["state"] = "completed"
            final["success_count"] = result.success_images
            final["error_count"] = result.error_images
            final["skipped_count"] = result.skipped_images
            final["video_count"] = result.video_images
            final["finished_at"] = _dt.now().isoformat()
            _atomic_write_json(progress_file, final)

        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()


def start_background_card_generation(
    products: list[dict],
    *,
    mode: str = "translate",
    custom_prompt: str = "",
    progress_file: str = "card_image_progress.json",
) -> None:
    """在后台 daemon 线程中启动 AI 管线图片生成/翻译。

    主线程可通过 read_progress(progress_file) 轮询进度。
    """
    import asyncio as _asyncio
    from datetime import datetime as _dt

    from image_generator import generate_batch_cards

    initial = _build_initial_image_progress(products)
    _atomic_write_json(progress_file, initial)

    def _run_in_thread():
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)

        async def _run():
            def on_asin_complete(asin_result):
                data = _serialize_asin_card_result(asin_result)
                _update_progress_with_asin(progress_file, data)

            result = await generate_batch_cards(
                products=products,
                progress_callback=on_asin_complete,
                resume_from=progress_file,
                mode=mode,
                custom_prompt=custom_prompt,
            )

            final = read_progress(progress_file) or {}
            final["state"] = "completed"
            final["success_count"] = result.success_cards
            final["error_count"] = result.error_cards
            final["skipped_count"] = result.skipped_cards
            final["video_count"] = result.video_cards
            final["finished_at"] = _dt.now().isoformat()
            _atomic_write_json(progress_file, final)

        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()
