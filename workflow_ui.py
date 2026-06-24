"""
工作流 UI — Streamlit Tab5 渲染层。

提供 render_workflow_tab() 主入口函数，
供 app.py 在 `with tab5:` 块中调用。

UI 区域（从上到下）：
  ① 上传区 — file_uploader + ASIN 校验
  ② 环节选择区 — checkboxes 自由勾选
  ③ 流水线可视化 — 横向阶段流程图
  ④ 启动按钮 — 校验 API 配置
  ⑤ 进度面板 — 轮询 workflow_progress.json
  ⑥ 各阶段结果卡 — 状态/摘要/下载
  ⑦ 错误 & 重试
  ⑧ 下载汇总
"""

from __future__ import annotations

import os
import time as _time
import tempfile

import streamlit as st

from workflow_engine import (
    PIPELINE_ORDER,
    STAGE_LABELS,
    WorkflowStage,
    StageStatus,
    read_workflow_progress,
    run_workflow_background,
    validate_workflow_config,
    _atomic_write_json,
)


# ══════════════════════════════════════════════════════════════════════
# Session State 初始化
# ══════════════════════════════════════════════════════════════════════

def _init_session_state():
    """初始化工作流相关的 session_state 键。"""
    defaults = {
        "wf_asins": [],
        "wf_running": False,
        "wf_polling": False,
        "wf_progress_file": "workflow_progress.json",
        "wf_completed": False,
        "wf_custom_prompt": "",
    }
    for key, default in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default


# ══════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════

def render_workflow_tab(db_path: str, html_dir: str):
    """渲染工作流 Tab 全部 UI。

    Args:
        db_path: SQLite 数据库文件路径。
        html_dir: HTML 存档目录路径。
    """
    _init_session_state()

    st.header("🔄 工作流（Pipeline）")
    st.caption("勾选环节 → 上传 Excel → 一键启动，自动按顺序执行")

    # ── ② 环节选择区（始终可见，在最上方）──
    enabled_stages, image_mode = _render_stage_selection()

    # ── ③ 流水线可视化（始终可见）──
    _render_pipeline_diagram(enabled_stages)

    # ── ① 上传区 ──
    _render_upload_section()

    # ── ④ 启动按钮（始终可见，无文件时禁用）──
    _render_start_button(enabled_stages, image_mode, db_path, html_dir)

    # ── ⑤ 进度面板 + ⑥ 结果卡 + ⑦ 错误重试 + ⑧ 下载汇总 ──
    _render_progress_and_results(enabled_stages)


# ══════════════════════════════════════════════════════════════════════
# ① 上传区
# ══════════════════════════════════════════════════════════════════════

def _render_upload_section():
    """渲染文件上传 & ASIN 校验。始终显示上传组件。"""
    uploaded_file = st.file_uploader(
        "📁 上传包含 ASIN 列的 Excel 文件",
        type=["xlsx"],
        help="Excel 第一行必须是表头，且包含 'asin' 列（大小写不敏感）",
        key="wf_uploader",
    )

    if uploaded_file is None:
        # 文件被清除 → 重置
        if st.session_state.wf_asins:
            st.session_state.wf_asins = []
    else:
        # 保存到临时文件并校验
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(uploaded_file.getvalue())
            temp_path = tmp.name

        try:
            from crawler_ui import validate_upload
            result = validate_upload(temp_path)

            if not result.is_valid:
                st.error(f"❌ {result.error}")
                st.session_state.wf_asins = []
            else:
                st.session_state.wf_asins = result.asins
                st.success(f"✅ 检测到 **{result.count}** 个 ASIN")

                # 显示 ASIN 列表（可折叠）
                with st.expander(f"📋 ASIN 列表（{result.count} 个）", expanded=False):
                    cols = st.columns(5)
                    for i, asin in enumerate(result.asins):
                        with cols[i % 5]:
                            st.code(asin, language=None)
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


# ══════════════════════════════════════════════════════════════════════
# ② 环节选择区
# ══════════════════════════════════════════════════════════════════════

def _render_stage_selection() -> tuple[list[str], str]:
    """渲染环节选择 checkboxes。

    Returns:
        (enabled_stages, image_translator_mode)
    """
    st.divider()
    st.subheader("⚙️ 环节选择")
    st.caption("勾选要执行的环节，未勾选的自动跳过。按从上到下的顺序依次执行。")

    enabled = []

    col_check, col_hint = st.columns([1, 3])

    with col_check:
        do_crawl = st.checkbox(
            "🕷️ 爬虫采集",
            value=True,
            help="采集 Amazon 产品信息 + 保存 HTML 存档",
            key="wf_chk_crawl",
        )
    with col_hint:
        st.caption("使用 Playwright/ScraperAPI 采集 Amazon 产品页，保存 HTML 存档和产品基本信息。")

    if do_crawl:
        enabled.append(WorkflowStage.CRAWL.value)

    col_check2, col_hint2 = st.columns([1, 3])
    with col_check2:
        do_phase1 = st.checkbox(
            "🔍 信息萃取 Phase1",
            value=True,
            help="从 HTML 提取产品属性（品类/材质/功能卖点等）",
            key="wf_chk_phase1",
        )
    with col_hint2:
        st.caption("AI 读取 HTML 页面，提取 15+ 个结构化字段（品类、材质、功能、场景等），写入数据库。")

    if do_phase1:
        enabled.append(WorkflowStage.PHASE1_EXTRACTION.value)

    col_check3, col_hint3 = st.columns([1, 3])
    with col_check3:
        do_phase2 = st.checkbox(
            "✍️ 文案生成 Phase2",
            value=True,
            help="生成俄语标题 + 详情 + 搜索词",
            key="wf_chk_phase2",
        )
    with col_hint3:
        st.caption("AI 基于 Phase1 萃取的结构化信息，生成符合 Wildberries SEO 的俄语文案。")

    if do_phase2:
        enabled.append(WorkflowStage.PHASE2_GENERATION.value)

    col_check4, col_hint4 = st.columns([1, 3])
    with col_check4:
        do_image = st.checkbox(
            "🖼️ 图片翻译",
            value=False,
            help="翻译产品图片中的英文文字为俄文",
            key="wf_chk_image",
        )
    with col_hint4:
        st.caption("下载产品图片，将图中的英文翻译为俄文后重新上传。")

    image_mode = "ai"
    if do_image:
        enabled.append(WorkflowStage.IMAGE_TRANSLATION.value)
        image_mode = st.radio(
            "图片翻译管线",
            options=["ai", "traditional"],
            format_func=lambda x: (
                "🤖 AI 管线（Gemini/中转站 直接翻译图中文字）"
                if x == "ai"
                else "📐 传统管线（OCR → 翻译 → 擦除 → 覆写）"
            ),
            horizontal=True,
            key="wf_image_mode_radio",
        )

    col_check5, col_hint5 = st.columns([1, 3])
    with col_check5:
        do_cards = st.checkbox(
            "🎨 产品卡片设计",
            value=False,
            help="用产品图生成 Wildberries 风格卡片图",
            key="wf_chk_cards",
        )
    with col_hint5:
        st.caption("AI 视觉模型直接生成 WB 风格产品卡片（3:4 比例，俄语文案叠加）。")

    if do_cards:
        enabled.append(WorkflowStage.IMAGE_CARD_DESIGN.value)

        # 卡片设计自定义提示词
        st.text_area(
            "💬 卡片设计自定义提示词（可选）",
            value=st.session_state.wf_custom_prompt,
            height=80,
            placeholder="输入额外的设计要求，如：背景用深色、添加价格标签...",
            key="wf_custom_prompt_input",
        )
        st.session_state.wf_custom_prompt = st.session_state.wf_custom_prompt_input

    return enabled, image_mode


# ══════════════════════════════════════════════════════════════════════
# ③ 流水线可视化
# ══════════════════════════════════════════════════════════════════════

def _render_pipeline_diagram(enabled_stages: list[str]):
    """渲染横向阶段流程图，彩色状态标记。"""
    st.divider()
    st.subheader("📊 流水线")

    # 读取当前进度（如果有的话）
    progress = None
    if st.session_state.wf_polling or st.session_state.wf_completed:
        progress = read_workflow_progress(st.session_state.wf_progress_file)

    stages_data = progress.get("stages", {}) if progress else {}

    # 构建每个阶段的显示状态（用 markdown 一行流式展示）
    segments = []
    for stage in PIPELINE_ORDER:
        is_enabled = stage.value in enabled_stages
        stage_info = stages_data.get(stage.value, {})
        status = stage_info.get("status", "not_started")

        # 确定图标
        if not is_enabled:
            icon = "⊘"
        elif status == StageStatus.DONE.value:
            icon = "✅"
        elif status == StageStatus.RUNNING.value:
            icon = "🔄"
        elif status == StageStatus.ERROR.value:
            icon = "❌"
        elif status == StageStatus.SKIPPED.value:
            icon = "⏭️"
        else:
            icon = "⏳"

        # 阶段简称
        short_name = {
            WorkflowStage.CRAWL.value: "爬虫",
            WorkflowStage.PHASE1_EXTRACTION.value: "Phase1",
            WorkflowStage.PHASE2_GENERATION.value: "Phase2",
            WorkflowStage.IMAGE_TRANSLATION.value: "图片",
            WorkflowStage.IMAGE_CARD_DESIGN.value: "卡片",
        }.get(stage.value, stage.value)

        segments.append(f"{icon} **{short_name}**")

    st.markdown(" → ".join(segments))
    st.caption("✅完成  🔄运行中  ⏳等待  ❌失败  ⊘跳过")


# ══════════════════════════════════════════════════════════════════════
# ④ 启动按钮
# ══════════════════════════════════════════════════════════════════════

def _render_start_button(
    enabled_stages: list[str],
    image_mode: str,
    db_path: str,
    html_dir: str,
):
    """渲染启动按钮（含 API 配置校验）。"""
    st.divider()

    # 如果正在运行或已完成，不显示启动按钮
    if st.session_state.wf_polling:
        return
    if st.session_state.wf_completed:
        if st.button("🔄 重新运行工作流", use_container_width=True):
            _reset_workflow()
            st.rerun()
        return

    if not enabled_stages:
        st.info("👆 请至少勾选一个环节")
        return

    has_file = bool(st.session_state.wf_asins)

    # API 配置校验
    api_config = _collect_api_config()
    warnings = validate_workflow_config(enabled_stages, api_config)

    col_btn, col_info = st.columns([1, 2])

    with col_btn:
        # 确定禁用原因
        disabled_reasons = []
        if not has_file:
            disabled_reasons.append("请上传 Excel 文件")
        if warnings:
            disabled_reasons.extend(warnings)

        start_disabled = len(disabled_reasons) > 0

        if start_disabled:
            # 显示禁用原因
            for reason in disabled_reasons[:2]:  # 最多显示 2 条
                st.caption(reason)

        if st.button(
            "🚀 启动工作流",
            type="primary",
            use_container_width=True,
            disabled=start_disabled,
        ):
            # 捕获爬虫配置
            crawler_config = {
                "headless": st.session_state.get("crawler_mode", "playwright") != "playwright"
                or True,  # 工作流中默认 headless
                "delay_min": 3.0,
                "delay_max": 8.0,
                "crawler_mode": st.session_state.get("crawler_mode", "playwright"),
                "scraperapi_key": st.session_state.get("scraperapi_key", ""),
            }

            # 字体配置
            font_config = {
                "font_name": "Roboto-Regular.ttf",
                "auto_size": True,
                "manual_size": 24,
            }

            # 清理旧进度文件
            progress_file = st.session_state.wf_progress_file
            if os.path.isfile(progress_file):
                os.remove(progress_file)

            # 启动后台工作流
            run_workflow_background(
                asins=st.session_state.wf_asins,
                enabled_stages=enabled_stages,
                image_translator_mode=image_mode,
                db_path=db_path,
                html_dir=html_dir,
                progress_file=progress_file,
                crawler_config=crawler_config,
                api_config=api_config,
                font_config=font_config,
                custom_prompt=st.session_state.wf_custom_prompt,
            )

            st.session_state.wf_running = True
            st.session_state.wf_polling = True
            st.session_state.wf_completed = False
            st.rerun()

    with col_info:
        if warnings:
            for w in warnings:
                st.warning(w)
        else:
            stage_count = len(enabled_stages)
            stage_names = [
                STAGE_LABELS.get(
                    next((s for s in PIPELINE_ORDER if s.value == v), None), v
                )
                for v in enabled_stages
            ]
            st.success(
                f"✅ 配置就绪，将依次执行 **{stage_count}** 个环节：\n\n"
                + " → ".join(stage_names)
            )


# ══════════════════════════════════════════════════════════════════════
# ⑤⑥⑦⑧ 进度面板 + 结果卡 + 错误重试 + 下载汇总
# ══════════════════════════════════════════════════════════════════════

def _render_progress_and_results(enabled_stages: list[str]):
    """渲染进度轮询和结果展示。"""
    if not st.session_state.wf_polling and not st.session_state.wf_completed:
        return

    progress_file = st.session_state.wf_progress_file
    progress = read_workflow_progress(progress_file)

    if progress is None:
        st.warning("⚠️ 进度文件丢失，请重新启动工作流。")
        _reset_workflow()
        st.rerun()
        return

    overall_state = progress.get("overall_state", "idle")
    stages_data = progress.get("stages", {})

    st.divider()
    st.subheader("📊 执行进度")

    # ── ⑤ 整体进度条 ──
    total_stages = len([s for s in enabled_stages])
    done_stages = len([
        s for s in enabled_stages
        if stages_data.get(s, {}).get("status") in (
            StageStatus.DONE.value, StageStatus.ERROR.value
        )
    ])

    if total_stages > 0:
        pct = done_stages / total_stages
    else:
        pct = 0

    current_stage_key = progress.get("current_stage", "")
    current_label = STAGE_LABELS.get(
        next((s for s in PIPELINE_ORDER if s.value == current_stage_key), None),
        "",
    )

    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1:
        st.metric("进度", f"{done_stages}/{total_stages} 阶段")
    with col_m2:
        st.metric("当前", current_label or "—")
    with col_m3:
        started = progress.get("started_at", "")
        if started and overall_state == "running":
            try:
                elapsed = _time.time() - _dt_parse(started)
                st.metric("已用时", _format_duration(int(elapsed)))
            except Exception:
                st.metric("已用时", "—")
        else:
            st.metric("状态", "✅ 完成" if overall_state == "completed" else overall_state)

    st.progress(pct)

    # ── ⑥ 各阶段结果卡 ──
    st.divider()
    st.subheader("📋 阶段详情")

    for stage in PIPELINE_ORDER:
        if stage.value not in enabled_stages:
            continue

        stage_data = stages_data.get(stage.value, {})
        status = stage_data.get("status", "not_started")
        label = STAGE_LABELS.get(stage, stage.value)

        _render_stage_card(stage, label, stage_data)

    # ── ⑦ 错误 & 重试 ──
    error_stages = [
        (stage, stages_data.get(stage.value, {}))
        for stage in PIPELINE_ORDER
        if stage.value in enabled_stages
        and stages_data.get(stage.value, {}).get("status") == StageStatus.ERROR.value
    ]
    if error_stages:
        _render_error_retry_panel(error_stages, enabled_stages, progress)

    # ── ⑧ 下载汇总 ──
    if overall_state in ("completed", "error"):
        _render_download_summary(progress, enabled_stages)
        st.session_state.wf_polling = False
        st.session_state.wf_completed = True
        st.session_state.wf_running = False
    elif overall_state == "running":
        # 继续轮询
        _time.sleep(5)
        st.rerun()


def _render_stage_card(
    stage: WorkflowStage,
    label: str,
    stage_data: dict,
):
    """渲染单个阶段的执行结果卡片。"""
    status = stage_data.get("status", "not_started")

    # 状态图标
    icon = {
        StageStatus.DONE.value: "✅",
        StageStatus.RUNNING.value: "🔄",
        StageStatus.ERROR.value: "❌",
        StageStatus.SKIPPED.value: "⏭️",
        StageStatus.NOT_STARTED.value: "⏳",
    }.get(status, "❓")

    # 耗时
    started = stage_data.get("started_at", "")
    finished = stage_data.get("finished_at", "")
    duration_str = ""
    if started and finished:
        try:
            dur = _dt_parse(finished) - _dt_parse(started)
            duration_str = f" | 耗时 {_format_duration(int(dur))}"
        except Exception:
            pass

    summary = stage_data.get("summary", "")
    error = stage_data.get("error", "")

    with st.expander(f"{icon} {label} — {summary}{duration_str}", expanded=(status in ("running", "error"))):
        if error:
            st.error(f"```\n{error[:2000]}\n```")

        # 下载按钮
        download_path = stage_data.get("download_temp_path", "")
        download_label = stage_data.get("download_label", "")
        download_filename = stage_data.get("download_filename", "")

        if download_path and os.path.isfile(download_path) and download_label:
            with open(download_path, "rb") as f:
                download_bytes = f.read()
            st.download_button(
                label=download_label,
                data=download_bytes,
                file_name=download_filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"wf_dl_{stage.value}",
            )


def _render_error_retry_panel(
    error_stages: list[tuple[WorkflowStage, dict]],
    enabled_stages: list[str],
    progress: dict,
):
    """渲染错误和重试面板。"""
    st.divider()
    st.subheader("⚠️ 错误 & 重试")

    for stage, stage_data in error_stages:
        error_msg = stage_data.get("error", "未知错误")
        # 只显示错误的第一行
        first_line = error_msg.split("\n")[0][:200]
        st.error(f"**{STAGE_LABELS.get(stage, stage.value)}**: {first_line}")

    # 重试按钮 — 从第一个失败的阶段开始
    first_error_stage = error_stages[0][0]

    if st.button(
        f"🔄 从「{STAGE_LABELS.get(first_error_stage, first_error_stage.value)}」重新执行",
        type="secondary",
        use_container_width=True,
    ):
        _retry_from_stage(first_error_stage, enabled_stages, progress)
        st.rerun()


def _render_download_summary(progress: dict, enabled_stages: list[str]):
    """渲染下载汇总区。"""
    st.divider()
    st.subheader("📦 下载汇总")

    stages_data = progress.get("stages", {})
    downloads_found = False

    cols = st.columns(min(4, len(enabled_stages)))
    col_idx = 0

    for stage in PIPELINE_ORDER:
        if stage.value not in enabled_stages:
            continue
        stage_data = stages_data.get(stage.value, {})
        if stage_data.get("status") != StageStatus.DONE.value:
            continue

        download_path = stage_data.get("download_temp_path", "")
        download_label = stage_data.get("download_label", "")
        download_filename = stage_data.get("download_filename", "")

        if download_path and os.path.isfile(download_path) and download_label:
            with open(download_path, "rb") as f:
                download_bytes = f.read()
            with cols[col_idx % len(cols)]:
                st.download_button(
                    label=download_label,
                    data=download_bytes,
                    file_name=download_filename,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key=f"wf_summary_dl_{stage.value}",
                )
            col_idx += 1
            downloads_found = True

    if not downloads_found:
        st.caption("暂无可用下载")


# ══════════════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════════════

def _collect_api_config() -> dict:
    """从 session_state 收集所有 API 配置。"""
    return {
        "p1_provider": st.session_state.get("p1_provider", ""),
        "p1_api_key": st.session_state.get("p1_api_key", ""),
        "p1_base_url": st.session_state.get("p1_base_url", ""),
        "p1_model": st.session_state.get("p1_model", ""),
        "p2_provider": st.session_state.get("p2_provider", ""),
        "p2_api_key": st.session_state.get("p2_api_key", ""),
        "p2_base_url": st.session_state.get("p2_base_url", ""),
        "p2_model": st.session_state.get("p2_model", ""),
        "image_gen_provider": st.session_state.get("image_gen_provider", ""),
        "image_gen_api_key": st.session_state.get("image_gen_api_key", ""),
        "image_gen_base_url": st.session_state.get("image_gen_base_url", ""),
        "image_gen_model": st.session_state.get("image_gen_model", ""),
        "gemini_api_key": st.session_state.get("gemini_api_key", ""),
        "gemini_model": st.session_state.get("gemini_model", ""),
        "gemini_proxy": st.session_state.get("gemini_proxy", ""),
    }


def _reset_workflow():
    """重置工作流状态。"""
    st.session_state.wf_running = False
    st.session_state.wf_polling = False
    st.session_state.wf_completed = False
    # 清理进度文件
    progress_file = st.session_state.wf_progress_file
    if os.path.isfile(progress_file):
        try:
            os.remove(progress_file)
        except OSError:
            pass


def _retry_from_stage(
    failed_stage: WorkflowStage,
    enabled_stages: list[str],
    progress: dict,
):
    """从失败阶段开始重试。

    重置失败阶段及之后所有已启用阶段的状态，
    然后重新启动后台工作流。
    """
    # 找到失败阶段在 PIPELINE_ORDER 中的索引
    failed_idx = PIPELINE_ORDER.index(failed_stage)

    # 重置失败阶段及其之后的所有已启用阶段
    stages_data = progress.get("stages", {})
    for i in range(failed_idx, len(PIPELINE_ORDER)):
        stage = PIPELINE_ORDER[i]
        if stage.value in enabled_stages:
            stages_data[stage.value] = {
                "status": StageStatus.NOT_STARTED.value,
                "started_at": "",
                "finished_at": "",
                "error": "",
                "summary": "",
                "download_label": "",
                "download_filename": "",
                "download_temp_path": "",
            }

    # 更新进度文件
    progress["stages"] = stages_data
    progress["overall_state"] = "idle"
    progress["current_stage"] = ""
    _atomic_write_json(st.session_state.wf_progress_file, progress)

    # 重新启动（保留之前的中间数据如 crawl_products_json_path）
    api_config = _collect_api_config()
    crawler_config = {
        "headless": True,
        "delay_min": 3.0,
        "delay_max": 8.0,
        "crawler_mode": st.session_state.get("crawler_mode", "playwright"),
        "scraperapi_key": st.session_state.get("scraperapi_key", ""),
    }
    font_config = {
        "font_name": "Roboto-Regular.ttf",
        "auto_size": True,
        "manual_size": 24,
    }

    run_workflow_background(
        asins=st.session_state.wf_asins,
        enabled_stages=enabled_stages,
        image_translator_mode=progress.get("image_translator_mode", "ai"),
        db_path="products.db",
        html_dir="html",
        progress_file=st.session_state.wf_progress_file,
        crawler_config=crawler_config,
        api_config=api_config,
        font_config=font_config,
        custom_prompt=st.session_state.wf_custom_prompt,
    )

    st.session_state.wf_running = True
    st.session_state.wf_polling = True
    st.session_state.wf_completed = False


def _dt_parse(iso_str: str) -> float:
    """解析 ISO 时间字符串为 Unix timestamp。"""
    from datetime import datetime
    try:
        # 处理各种 ISO 格式变体
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


def _format_duration(seconds: int) -> str:
    """格式化时长。"""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m{s}s" if s > 0 else f"{m}m"
    else:
        h, r = divmod(seconds, 3600)
        m, s = divmod(r, 60)
        return f"{h}h{m}m"
