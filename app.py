"""
WB Content Tool — Streamlit Web 界面。

功能 Tab：
  - 爬虫采集：上传 ASIN 列表 → 采集亚马逊产品 → 预览 + 下载
  - 文案翻译：两阶段 AI 流水线（信息萃取 + 文案生成）
  - 图片翻译：双管线 — 传统管线（OCR+翻译+覆写）或 AI 管线（中转站/Gemini 直译）
  - 产品卡片设计：AI 生成 Wildberries 风格产品卡片图
"""

import asyncio
import os
import tempfile
import time as _time

import streamlit as st

from config import settings
from crawler_ui import CrawlResult, UploadResult, execute_crawl, generate_download, validate_upload
from image_processor import FontConfig
from image_translator_ui import (
    ImageUploadResult,
    enrich_product_context_from_db,
    generate_merged_excel,
    generate_output_excel,
    generate_report,
    run_image_translation,
    validate_image_upload,
)
from image_generator_ui import (
    CardGenerationUploadResult,
    generate_card_output_excel,
    generate_card_report,
    retry_single_card as retry_single_card_gen,
    run_card_generation,
    run_card_generation_from_images,
    validate_card_generation_upload,
    _find_product_context,
    _replace_and_recount_cards,
)
from translator_ui import (
    TranslationResult,
    TranslationUploadResult,
    execute_phase1_extraction,
    execute_phase2_generation,
    execute_translation,
    generate_phase2_download,
    generate_translation_download,
    validate_translation_upload,
)
from worker import (
    read_progress,
    start_background_card_generation,
    start_background_translation,
)
from workflow_ui import render_workflow_tab

# ============================================================
# 页面配置
# ============================================================

st.set_page_config(
    page_title="WB Content Tool",
    page_icon="🛒",
    layout="wide",
)

st.title("WB Content Tool")
st.caption("亚马逊 → Wildberries 商品信息采集与翻译")

# 初始化持久化 session_state
if "image_results" not in st.session_state:
    st.session_state.image_results = None
if "image_upload_info" not in st.session_state:
    st.session_state.image_upload_info = None
if "image_products" not in st.session_state:
    st.session_state.image_products = None
if "image_font_config" not in st.session_state:
    st.session_state.image_font_config = None
if "image_original_bytes" not in st.session_state:
    st.session_state.image_original_bytes = None
# Tab3 AI 管线（中转站/Gemini 图片翻译）session_state
if "image_ai_original_bytes" not in st.session_state:
    st.session_state.image_ai_original_bytes = None
if "image_ai_results" not in st.session_state:
    st.session_state.image_ai_results = None
if "image_ai_products" not in st.session_state:
    st.session_state.image_ai_products = None
if "image_ai_upload_info" not in st.session_state:
    st.session_state.image_ai_upload_info = None
# Tab3 实时进度轮询
if "image_polling" not in st.session_state:
    st.session_state.image_polling = False
if "image_progress_file" not in st.session_state:
    st.session_state.image_progress_file = ""
if "image_ai_polling" not in st.session_state:
    st.session_state.image_ai_polling = False
if "image_ai_progress_file" not in st.session_state:
    st.session_state.image_ai_progress_file = ""

# Tab4 产品卡片设计
if "card_gen_custom_prompt" not in st.session_state:
    st.session_state.card_gen_custom_prompt = ""
if "card_gen_results" not in st.session_state:
    st.session_state.card_gen_results = None
if "card_gen_uploaded_images" not in st.session_state:
    st.session_state.card_gen_uploaded_images = None

# 数据库文件路径（项目根目录下的 products.db）
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "products.db")


# ============================================================
# 辅助函数：实时进度表格展示 & 结果重建
# ============================================================

def _show_image_progress_table(progress: dict):
    """根据进度文件数据显示图片翻译进度表格。

    每张图片一行，含 ASIN、序号、原始URL、状态图标、错误信息。
    """
    asin_results = progress.get("asin_results", {})

    rows = []
    for asin_key, ar in asin_results.items():
        for img in ar.get("images", []):
            status = img["status"]
            status_icon = {
                "pending": "⏳",
                "ok": "✅",
                "error": "❌",
                "skipped": "⏭️",
                "video": "🎬",
            }.get(status, "❓")

            url = img.get("original_url", "")
            rows.append({
                "ASIN": asin_key,
                "序号": img.get("index", 0),
                "原始URL": url[:80] + "..." if len(url) > 80 else url,
                "状态": f"{status_icon} {status}",
                "错误": img.get("error", "")[:50],
            })

    if rows:
        import pandas as pd
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)


def _build_image_results_from_progress(progress: dict):
    """从进度文件数据重建 BatchImageResult（传统管线）。"""
    from image_translator import BatchImageResult, AsinImageResult, ImageResult

    asin_results_data = progress.get("asin_results", {})
    results = []

    for asin_key, ar in asin_results_data.items():
        images = []
        for img in ar.get("images", []):
            images.append(ImageResult(
                index=img.get("index", 0),
                original_url=img.get("original_url", ""),
                r2_url=img.get("r2_url", ""),
                status=img.get("status", "pending"),
                error=img.get("error", ""),
            ))

        results.append(AsinImageResult(
            asin=asin_key,
            images=images,
            success_count=sum(1 for i in images if i.status == "ok"),
            error_count=sum(1 for i in images if i.status == "error"),
            skipped_count=sum(1 for i in images if i.status == "skipped"),
            video_count=sum(1 for i in images if i.status == "video"),
        ))

    return BatchImageResult(
        results=results,
        total_asins=progress.get("total_asins", 0),
        completed_asins=len(results),
        total_images=progress.get("total_images", 0),
        success_images=progress.get("success_count", 0),
        error_images=progress.get("error_count", 0),
        skipped_images=progress.get("skipped_count", 0),
        video_images=progress.get("video_count", 0),
        started_at=progress.get("started_at", ""),
        finished_at=progress.get("finished_at", ""),
    )


def _build_card_results_from_progress(progress: dict):
    """从进度文件数据重建 BatchCardResult（AI 管线）。"""
    from image_generator import BatchCardResult, AsinCardResult, CardResult

    asin_results_data = progress.get("asin_results", {})
    results = []

    for asin_key, ar in asin_results_data.items():
        cards = []
        for card in ar.get("images", []):  # 注意：AI 管线中 key 仍为 "images"
            cards.append(CardResult(
                index=card.get("index", 0),
                original_url=card.get("original_url", ""),
                r2_url=card.get("r2_url", ""),
                status=card.get("status", "pending"),
                error=card.get("error", ""),
            ))

        results.append(AsinCardResult(
            asin=asin_key,
            cards=cards,
            success_count=sum(1 for c in cards if c.status == "ok"),
            error_count=sum(1 for c in cards if c.status == "error"),
            skipped_count=sum(1 for c in cards if c.status == "skipped"),
            video_count=sum(1 for c in cards if c.status == "video"),
        ))

    return BatchCardResult(
        results=results,
        total_asins=progress.get("total_asins", 0),
        completed_asins=len(results),
        total_cards=progress.get("total_images", 0),
        success_cards=progress.get("success_count", 0),
        error_cards=progress.get("error_count", 0),
        skipped_cards=progress.get("skipped_count", 0),
        video_cards=progress.get("video_count", 0),
        started_at=progress.get("started_at", ""),
        finished_at=progress.get("finished_at", ""),
    )

# Phase 1/2 工作流 session_state
if "phase1_done" not in st.session_state:
    st.session_state.phase1_done = False
if "phase1_results" not in st.session_state:
    st.session_state.phase1_results = None
if "phase2_done" not in st.session_state:
    st.session_state.phase2_done = False
if "phase2_results" not in st.session_state:
    st.session_state.phase2_results = None
if "phase2_download_data" not in st.session_state:
    st.session_state.phase2_download_data = None

# ============================================================
# 侧边栏 — 爬虫配置
# ============================================================

with st.sidebar:
    st.header("⚙️ 爬虫配置")

    # 初始化爬虫 session_state
    if "crawler_mode" not in st.session_state:
        st.session_state.crawler_mode = settings.crawler_mode
    if "scraperapi_key" not in st.session_state:
        st.session_state.scraperapi_key = settings.scraperapi_key

    crawler_mode = st.selectbox(
        "采集模式",
        options=["playwright", "scraperapi"],
        format_func=lambda x: "🖥️ Playwright（浏览器自动化）" if x == "playwright" else "☁️ ScraperAPI（付费代理）",
        index=0 if st.session_state.crawler_mode == "playwright" else 1,
        help="Playwright: 免费但需要 VPN；ScraperAPI: 付费但稳定可靠",
    )
    st.session_state.crawler_mode = crawler_mode

    if crawler_mode == "playwright":
        headless = st.checkbox(
            "无头模式（后台运行浏览器）",
            value=settings.crawler_headless,
            help="取消勾选可在浏览器窗口中看到采集过程",
        )

        st.subheader("请求间隔")
        col1, col2 = st.columns(2)
        with col1:
            delay_min = st.number_input(
                "最小间隔（秒）",
                min_value=0.5, max_value=30.0, value=3.0, step=0.5,
            )
        with col2:
            delay_max = st.number_input(
                "最大间隔（秒）",
                min_value=1.0, max_value=60.0, value=8.0, step=0.5,
            )
        if delay_min > delay_max:
            st.warning("最小间隔不能大于最大间隔")
    else:
        headless = True
        delay_min, delay_max = 0.0, 0.0

        scraperapi_key = st.text_input(
            "ScraperAPI Key",
            value=st.session_state.scraperapi_key,
            type="password",
            placeholder="输入 ScraperAPI Key...",
            help="从 https://www.scraperapi.com 注册获取",
        )
        st.session_state.scraperapi_key = scraperapi_key

    st.divider()

    # --- Phase 1 API 配置（信息萃取 + 图片翻译传统管线）---
    st.header("🔍 Phase 1 API — 信息萃取 & 图片翻译")
    st.caption("Tab2 信息萃取 + Tab3 传统管线翻译 共用此 API")

    # 初始化 session_state 中的 Phase 1 配置
    if "p1_provider" not in st.session_state:
        st.session_state.p1_provider = settings.phase1_api_provider
    if "p1_api_key" not in st.session_state:
        st.session_state.p1_api_key = settings.phase1_api_key
    if "p1_base_url" not in st.session_state:
        st.session_state.p1_base_url = settings.phase1_api_base_url
    if "p1_model" not in st.session_state:
        st.session_state.p1_model = settings.phase1_model

    p1_provider = st.selectbox(
        "AI 服务商",
        options=["openai", "anthropic", "deepseek", "custom"],
        index=["openai", "anthropic", "deepseek", "custom"].index(
            st.session_state.p1_provider
        ) if st.session_state.p1_provider in ["openai", "anthropic", "deepseek", "custom"] else 0,
        key="p1_provider_select",
        help="Phase 1 用高性价比模型批量萃取即可",
    )
    st.session_state.p1_provider = p1_provider

    p1_api_key = st.text_input(
        "API Key",
        value=st.session_state.p1_api_key,
        type="password",
        placeholder="未配置时使用旧 TRANSLATE_API_KEY",
        key="p1_api_key_input",
    )
    st.session_state.p1_api_key = p1_api_key

    p1_base_url = st.text_input(
        "API Base URL",
        value=st.session_state.p1_base_url,
        placeholder="留空使用默认地址",
        key="p1_base_url_input",
    )
    st.session_state.p1_base_url = p1_base_url

    p1_model = st.text_input(
        "模型名称",
        value=st.session_state.p1_model,
        placeholder="如 deepseek-chat",
        key="p1_model_input",
    )
    st.session_state.p1_model = p1_model

    st.divider()

    # --- Phase 2 API 配置（文案生成）---
    st.header("✍️ Phase 2 API — 文案生成")
    st.caption("Tab2 俄语文案生成使用此 API")

    # 初始化 session_state 中的 Phase 2 配置
    if "p2_provider" not in st.session_state:
        st.session_state.p2_provider = settings.phase2_api_provider
    if "p2_api_key" not in st.session_state:
        st.session_state.p2_api_key = settings.phase2_api_key
    if "p2_base_url" not in st.session_state:
        st.session_state.p2_base_url = settings.phase2_api_base_url
    if "p2_model" not in st.session_state:
        st.session_state.p2_model = settings.phase2_model

    p2_enabled = st.checkbox(
        "独立配置 Phase 2（取消勾选则使用 Phase 1 设置）",
        value=bool(settings.phase2_api_key or settings.phase2_model != settings.phase1_model),
        key="p2_enabled",
        help="勾选后可为文案生成阶段使用不同的 AI 服务商和模型",
    )

    p2_provider = st.selectbox(
        "AI 服务商",
        options=["openai", "anthropic", "deepseek", "custom"],
        index=["openai", "anthropic", "deepseek", "custom"].index(
            st.session_state.p2_provider
        ) if st.session_state.p2_provider in ["openai", "anthropic", "deepseek", "custom"] else 0,
        key="p2_provider_select",
        help="Phase 2 建议用强模型精细写文案",
        disabled=not p2_enabled,
    )
    st.session_state.p2_provider = p2_provider

    p2_api_key = st.text_input(
        "API Key",
        value=st.session_state.p2_api_key if p2_enabled else "",
        type="password",
        placeholder="未配置时使用 Phase 1 设置",
        key="p2_api_key_input",
        disabled=not p2_enabled,
    )
    st.session_state.p2_api_key = p2_api_key

    p2_base_url = st.text_input(
        "API Base URL",
        value=st.session_state.p2_base_url if p2_enabled else "",
        placeholder="留空使用默认地址",
        key="p2_base_url_input",
        disabled=not p2_enabled,
    )
    st.session_state.p2_base_url = p2_base_url

    p2_model = st.text_input(
        "模型名称",
        value=st.session_state.p2_model if p2_enabled else "",
        placeholder="如 claude-opus-4-8",
        key="p2_model_input",
        disabled=not p2_enabled,
    )
    st.session_state.p2_model = p2_model

    if not p2_enabled:
        st.caption("ℹ️ Phase 2 将使用 Phase 1 的 API 配置")

    # --- 保存按钮 ---
    col_save1, col_save2 = st.columns(2)
    with col_save1:
        if st.button("💾 保存配置", use_container_width=True):
            settings.save_to_env(
                phase1_provider=p1_provider,
                phase1_api_key=p1_api_key,
                phase1_base_url=p1_base_url,
                phase1_model=p1_model,
                phase2_provider=p2_provider if p2_enabled else "",
                phase2_api_key=p2_api_key if p2_enabled else "",
                phase2_base_url=p2_base_url if p2_enabled else "",
                phase2_model=p2_model if p2_enabled else "",
                crawler_mode=st.session_state.crawler_mode,
                scraperapi_key=st.session_state.get("scraperapi_key", ""),
                image_gen_provider=st.session_state.get("image_gen_provider", ""),
                image_gen_api_key=st.session_state.get("image_gen_api_key", ""),
                image_gen_base_url=st.session_state.get("image_gen_base_url", ""),
                image_gen_model=st.session_state.get("image_gen_model", ""),
                image_gen_mode=st.session_state.get("image_gen_mode", ""),
                gemini_api_key=st.session_state.get("gemini_api_key", ""),
                gemini_model=st.session_state.get("gemini_model", ""),
                gemini_proxy=st.session_state.get("gemini_proxy", ""),
            )
            st.success("配置已保存到 .env 文件 ✅")
            # 同步到 settings（本次运行中生效）
            settings.phase1_api_provider = p1_provider
            settings.phase1_api_key = p1_api_key
            settings.phase1_api_base_url = p1_base_url
            settings.phase1_model = p1_model
            if p2_enabled:
                settings.phase2_api_provider = p2_provider
                settings.phase2_api_key = p2_api_key
                settings.phase2_api_base_url = p2_base_url
                settings.phase2_model = p2_model
            # 图片生成配置
            ip = st.session_state.get("image_gen_provider", "")
            if ip:
                settings.image_gen_provider = ip
            settings.image_gen_api_key = st.session_state.get("image_gen_api_key", "")
            settings.image_gen_base_url = st.session_state.get("image_gen_base_url", "")
            settings.image_gen_model = st.session_state.get("image_gen_model", "")
            settings.gemini_api_key = st.session_state.get("gemini_api_key", "")
            settings.gemini_model = st.session_state.get("gemini_model", "")
            settings.gemini_proxy = st.session_state.get("gemini_proxy", "")
    with col_save2:
        if st.button("🔄 重新加载", use_container_width=True):
            st.session_state.p1_provider = settings.phase1_api_provider
            st.session_state.p1_api_key = settings.phase1_api_key
            st.session_state.p1_base_url = settings.phase1_api_base_url
            st.session_state.p1_model = settings.phase1_model
            st.session_state.p2_provider = settings.phase2_api_provider
            st.session_state.p2_api_key = settings.phase2_api_key
            st.session_state.p2_base_url = settings.phase2_api_base_url
            st.session_state.p2_model = settings.phase2_model
            st.rerun()

    st.divider()
    st.caption('配置修改后点击"保存配置"生效')

    st.divider()

    # --- AI 图片 API 配置（中转站 / Gemini）---
    st.header("🎨 AI 图片 API — 中转站 / Gemini")
    st.caption("Tab3 AI 管线 + Tab4 卡片设计 共用此 API")

    # 提供商选择
    if "image_gen_provider" not in st.session_state:
        st.session_state.image_gen_provider = getattr(settings, 'image_gen_provider', 'gemini')
    if "image_gen_api_key" not in st.session_state:
        st.session_state.image_gen_api_key = getattr(settings, 'image_gen_api_key', '')
    if "image_gen_base_url" not in st.session_state:
        st.session_state.image_gen_base_url = getattr(settings, 'image_gen_base_url', '')
    if "image_gen_model" not in st.session_state:
        st.session_state.image_gen_model = getattr(settings, 'image_gen_model', '')
    if "image_gen_mode" not in st.session_state:
        st.session_state.image_gen_mode = getattr(settings, 'image_gen_mode', 'card_design')
    # Gemini 专用（向后兼容）
    if "gemini_api_key" not in st.session_state:
        st.session_state.gemini_api_key = settings.gemini_api_key
    if "gemini_model" not in st.session_state:
        st.session_state.gemini_model = settings.gemini_model
    if "gemini_proxy" not in st.session_state:
        st.session_state.gemini_proxy = getattr(settings, 'gemini_proxy', '')

    image_gen_provider = st.selectbox(
        "图片生成提供商",
        options=["gemini", "openai_compatible"],
        index=0 if st.session_state.image_gen_provider == "gemini" else 1,
        format_func=lambda x: "🪐 Gemini 2.5 Flash" if x == "gemini" else "🤖 OpenAI 兼容（中转站）",
        key="image_gen_provider_select",
    )
    st.session_state.image_gen_provider = image_gen_provider

    if image_gen_provider == "gemini":
        gemini_api_key = st.text_input(
            "Gemini API Key",
            value=st.session_state.gemini_api_key,
            type="password",
            key="gemini_api_key_input",
            placeholder="AI Studio 获取: https://aistudio.google.com",
        )
        st.session_state.gemini_api_key = gemini_api_key

        gemini_model = st.text_input(
            "模型名称",
            value=st.session_state.gemini_model,
            key="gemini_model_input",
            placeholder="gemini-2.5-flash-image",
        )
        st.session_state.gemini_model = gemini_model

        gemini_proxy = st.text_input(
            "代理地址（国内访问需要）",
            value=st.session_state.gemini_proxy,
            key="gemini_proxy_input",
            placeholder="http://127.0.0.1:10808",
        )
        st.session_state.gemini_proxy = gemini_proxy
    else:
        image_gen_api_key = st.text_input(
            "API Key",
            value=st.session_state.image_gen_api_key,
            type="password",
            key="image_gen_api_key_input",
            placeholder="sk-...",
        )
        st.session_state.image_gen_api_key = image_gen_api_key

        image_gen_base_url = st.text_input(
            "接口地址（Base URL）",
            value=st.session_state.image_gen_base_url,
            key="image_gen_base_url_input",
            placeholder="https://api.xxx.com",
        )
        st.session_state.image_gen_base_url = image_gen_base_url

        image_gen_model = st.text_input(
            "模型名称",
            value=st.session_state.image_gen_model,
            key="image_gen_model_input",
            placeholder="gpt-image-2",
        )
        st.session_state.image_gen_model = image_gen_model

    st.divider()

    # --- R2 图片存储状态 ---
    st.header("☁️ R2 图片存储")
    if settings.r2_access_key_id:
        st.success(f"✅ R2 已配置 — Bucket: `{settings.r2_bucket}`")
        st.caption(f"域名: {settings.r2_public_domain[:50]}...")
    else:
        st.warning("⚠️ R2 未配置，图片将保存到本地")

    st.divider()

    # --- 系统状态面板 ---
    with st.expander("🔧 系统状态", expanded=False):
        st.caption("各组件可用性检测（点击展开）")

        # 1. .env 配置
        if os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")):
            p1_configured = bool(settings.phase1_api_key)
            p2_configured = bool(settings.phase2_api_key)
            gemini_configured = bool(settings.gemini_api_key)
            r2_configured = bool(settings.r2_access_key_id)

            if p1_configured:
                st.success("✅ Phase 1 API 已配置")
            else:
                st.warning("⚠️ Phase 1 API 未配置 — 信息萃取和图片翻译不可用")

            if p2_configured or p1_configured:
                st.success("✅ Phase 2 API " + ("已独立配置" if p2_configured else "复用 Phase 1"))
            else:
                st.warning("⚠️ Phase 2 API 未配置 — 文案生成不可用")

            if gemini_configured or settings.image_gen_api_key:
                st.success("✅ AI 图片 API 已配置")
            else:
                st.info("ℹ️ AI 图片 API 未配置 — AI 图片管线不可用")

            if r2_configured:
                st.success("✅ R2 图片存储已配置")
            else:
                st.info("ℹ️ R2 未配置 — 图片仅保存在本地")
        else:
            st.warning("⚠️ .env 文件未找到 — 请点击「💾 保存配置」生成")

        # 2. Playwright
        @st.cache_resource(show_spinner=False)
        def _check_playwright():
            try:
                from playwright.sync_api import sync_playwright
                p = sync_playwright().start()
                b = p.chromium.launch(headless=True)
                b.close()
                p.stop()
                return True, ""
            except Exception as e:
                return False, str(e)

        pw_ok, pw_err = _check_playwright()
        if pw_ok:
            st.success("✅ Playwright 浏览器 — 爬虫可用")
        else:
            st.warning(f"⚠️ Playwright 浏览器不可用 — 爬虫请使用 ScraperAPI 模式")

        # 3. PaddleOCR
        @st.cache_resource(show_spinner=False)
        def _check_paddleocr():
            try:
                from paddleocr import PaddleOCR
                ocr = PaddleOCR(lang="en", show_log=False)
                return True, ""
            except Exception as e:
                return False, str(e)

        ocr_ok, ocr_err = _check_paddleocr()
        if ocr_ok:
            st.success("✅ PaddleOCR — 传统图片翻译可用")
        else:
            st.warning("⚠️ PaddleOCR 不可用 — 传统图片翻译管线不可用，请使用 AI 管线")

        # 4. 字体
        font_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
        if os.path.isdir(font_dir) and os.listdir(font_dir):
            st.success(f"✅ 字体文件 — {', '.join(os.listdir(font_dir)[:3])}")
        else:
            st.info("ℹ️ 字体目录为空 — 可在图片翻译 Tab 中指定系统字体")

        # 5. 网络
        @st.cache_data(show_spinner=False, ttl=60)
        def _check_network():
            try:
                import urllib.request
                urllib.request.urlopen("https://www.baidu.com", timeout=5)
                return True
            except Exception:
                return False

        if _check_network():
            st.success("✅ 网络连接正常")
        else:
            st.error("❌ 网络不可达 — AI API 调用将失败")

# ============================================================
# 主区域 — Tab 布局
# ============================================================

tab1, tab2, tab3, tab4, tab5 = st.tabs(["🕷️ 爬虫采集", "📝 文案翻译", "🖼️ 图片翻译", "🎨 产品卡片设计", "🔄 工作流"])

# ---------- Tab 1: 爬虫采集 ----------

with tab1:
    st.header("功能区 1：爬虫采集")

    uploaded_file = st.file_uploader(
        "上传包含 ASIN 列的 Excel 文件",
        type=["xlsx"],
        help="Excel 第一行必须是表头，且包含 'asin' 列（大小写不敏感）",
    )

    if uploaded_file is None:
        st.info("👆 请上传 Excel 文件开始")
    else:
        # --- 保存上传文件到临时路径 ---
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(uploaded_file.getvalue())
            temp_path = tmp.name

        try:
            # --- 校验 ---
            upload_result: UploadResult = validate_upload(temp_path)

            if not upload_result.is_valid:
                st.error(f"❌ {upload_result.error}")
            else:
                st.success(f"✅ 检测到 **{upload_result.count}** 个 ASIN")

                if st.button("🚀 开始采集", type="primary", use_container_width=True):
                    # --- 进度日志区域 ---
                    log_container = st.empty()
                    progress_bar = st.progress(0)
                    status_text = st.empty()

                    progress_entries = []

                    def on_progress(current: int, total: int, asin: str, status: str):
                        progress_entries.append(
                            {"current": current, "total": total, "asin": asin, "status": status}
                        )
                        # Streamlit 在 asyncio.run() 内无法实时更新 UI，
                        # 但回调记录的数据将在采集完成后展示。

                    # --- 执行采集 ---
                    # 同步爬虫配置到 settings
                    settings.crawler_mode = st.session_state.crawler_mode
                    if crawler_mode == "scraperapi":
                        settings.scraperapi_key = st.session_state.scraperapi_key

                    status_text.info("🔄 采集中，请耐心等待...")
                    crawl_result: CrawlResult = asyncio.run(
                        execute_crawl(
                            upload_result.asins,
                            headless=headless,
                            delay_min=delay_min,
                            delay_max=delay_max,
                            progress_callback=on_progress,
                        )
                    )

                    progress_bar.progress(100)
                    status_text.success(
                        f"✅ 采集完成！成功 {len(crawl_result.products)}/{upload_result.count}"
                    )

                    # --- 采集进度摘要 ---
                    with st.expander("📋 采集详情", expanded=False):
                        for entry in progress_entries:
                            emoji = "✅" if entry["status"] == "ok" else "❌"
                            st.text(
                                f"[{entry['current']}/{entry['total']}] "
                                f"{entry['asin']} {emoji}"
                            )

                    # --- 失败警告 ---
                    if crawl_result.failed_asins:
                        st.warning(
                            f"⚠️ 以下 {len(crawl_result.failed_asins)} 个 ASIN 采集失败："
                            f" {', '.join(crawl_result.failed_asins)}"
                        )

                    # --- 结果预览 ---
                    if crawl_result.products:
                        import pandas as pd
                        df = pd.DataFrame(crawl_result.products)
                        st.subheader(f"📊 采集结果预览（{len(df)} 条）")
                        st.dataframe(df, use_container_width=True, hide_index=True)

                        # --- 下载按钮 ---
                        xlsx_data = generate_download(crawl_result.products)
                        st.download_button(
                            label="📥 下载处理前表格（xlsx）",
                            data=xlsx_data,
                            file_name="爬虫表格（处理前）.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                        )
                    else:
                        st.error("没有采集到任何产品数据，请检查 ASIN 是否有效或稍后重试。")

        finally:
            # 清理临时文件
            try:
                os.unlink(temp_path)
            except OSError:
                pass

# ---------- Tab 2: 文案翻译（两阶段交互）----------

with tab2:
    st.header("功能区 2：文案翻译")
    st.caption("两阶段 AI 流水线：先信息萃取，再文案生成")

    uploaded_file = st.file_uploader(
        '上传「处理前」Excel 文件（包含 asin, 图片url, 标题, 详情 四列）',
        type=["xlsx"],
        help='上传爬虫采集生成的「处理前」表格，或符合四列格式的 Excel 文件',
    )

    if uploaded_file is None:
        # 文件被清除时清除旧结果
        if st.session_state.phase1_done:
            st.session_state.phase1_done = False
            st.session_state.phase1_results = None
            st.session_state.phase2_done = False
            st.session_state.phase2_results = None
            st.session_state.phase2_download_data = None
        st.info('👆 请上传「处理前」Excel 文件开始翻译')
    else:
        # --- 保存上传文件到临时路径 ---
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(uploaded_file.getvalue())
            temp_path = tmp.name

        try:
            # --- 校验 ---
            upload_result: TranslationUploadResult = validate_translation_upload(temp_path)

            if not upload_result.is_valid:
                st.error(f"❌ {upload_result.error}")
            else:
                asins = [p["asin"] for p in upload_result.products if p.get("asin")]
                st.success(f"✅ 检测到 **{upload_result.count}** 个待处理产品")

                # ============================================
                # 阶段一：AI 信息萃取
                # ============================================
                st.divider()
                st.subheader("🔍 阶段一：AI 信息萃取")

                # 检查 HTML 目录
                html_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "html")
                html_exists = os.path.isdir(html_dir)
                missing_html = []
                if html_exists:
                    missing_html = [a for a in asins if not os.path.isfile(os.path.join(html_dir, f"{a}.html"))]

                if not html_exists:
                    st.warning("⚠️ HTML 存档目录不存在，请先在「爬虫采集」Tab 中采集产品数据。")
                elif missing_html:
                    st.warning(
                        f"⚠️ 以下 {len(missing_html)} 个 ASIN 缺少 HTML 存档，"
                        f"将跳过萃取: {', '.join(missing_html[:5])}"
                        + ("..." if len(missing_html) > 5 else "")
                    )

                # Phase 1 按钮
                p1_disabled = not html_exists or not st.session_state.p1_api_key
                if p1_disabled and not st.session_state.p1_api_key:
                    st.caption("⚠️ 请先在侧边栏配置 Phase 1 API Key")

                if st.button(
                    "🔍 AI 信息萃取",
                    type="primary",
                    use_container_width=True,
                    disabled=p1_disabled,
                    key="phase1_btn",
                ):
                    # 同步配置到 settings
                    settings.phase1_api_provider = st.session_state.p1_provider
                    settings.phase1_api_key = st.session_state.p1_api_key
                    settings.phase1_api_base_url = st.session_state.p1_base_url
                    settings.phase1_model = st.session_state.p1_model

                    # 进度区域
                    p1_progress = st.progress(0)
                    p1_status = st.empty()
                    p1_status.info(f"🔄 Phase 1 萃取中... 共 {len(asins)} 个 ASIN")

                    # 执行 Phase 1
                    results = execute_phase1_extraction(
                        asins=asins,
                        html_dir=html_dir,
                        db_path=DB_PATH,
                        progress_callback=lambda cur, tot: p1_progress.progress(
                            int(cur / tot * 100) if tot > 0 else 0
                        ),
                    )

                    p1_progress.progress(100)
                    success_count = len([r for r in results if "error" not in r])
                    fail_count = len([r for r in results if "error" in r])
                    p1_status.success(
                        f"✅ Phase 1 萃取完成！成功 {success_count}/{len(asins)}"
                        + (f"，失败 {fail_count}" if fail_count > 0 else "")
                    )

                    # 存入 session_state
                    st.session_state.phase1_done = True
                    st.session_state.phase1_results = results
                    st.session_state.phase2_done = False
                    st.session_state.phase2_results = None
                    st.session_state.phase2_download_data = None
                    st.rerun()

                # Phase 1 结果展示
                if st.session_state.phase1_done and st.session_state.phase1_results:
                    results = st.session_state.phase1_results
                    success = [r for r in results if "error" not in r]
                    failed = [r for r in results if "error" in r]

                    # 读取数据库获取预览数据
                    import sqlite3
                    from db import get_product, init_db

                    db_path = DB_PATH
                    db = sqlite3.connect(db_path)
                    init_db(db)
                    preview_rows = []
                    for r in success:
                        product = get_product(db, r["asin"])
                        if product:
                            features = product.get("features", [])
                            usps = product.get("unique_selling_points", [])
                            scenarios = product.get("use_scenarios", [])
                            keywords = product.get("en_search_keywords", [])
                            specs = product.get("technical_specs", {})
                            preview_rows.append({
                                "ASIN": product.get("asin", ""),
                                "品类": product.get("category", ""),
                                "品牌": product.get("brand", ""),
                                "材质": product.get("material", ""),
                                "颜色": product.get("color", ""),
                                "尺寸": product.get("dimensions", ""),
                                "重量": product.get("weight", ""),
                                "容量": product.get("capacity", ""),
                                "包装内容": product.get("package_contents", ""),
                                "功能卖点": "\n".join(f"• {f}" for f in features) if features else "",
                                "技术参数": "\n".join(f"• {k}: {v}" for k, v in specs.items()) if specs else "",
                                "目标用户": product.get("target_audience", ""),
                                "使用场景": "\n".join(f"• {s}" for s in scenarios) if scenarios else "",
                                "差异化卖点": "\n".join(f"• {u}" for u in usps) if usps else "",
                                "搜索关键词": ", ".join(keywords) if keywords else "",
                            })
                    db.close()

                    if preview_rows:
                        import pandas as pd
                        st.caption(f"📊 结构化数据预览（{len(preview_rows)} 条，{len(preview_rows[0]) - 1} 个字段）")
                        df = pd.DataFrame(preview_rows)
                        st.dataframe(
                            df,
                            use_container_width=True,
                            hide_index=True,
                            height=min(400, 70 + 35 * len(preview_rows)),
                            column_config={
                                "功能卖点": st.column_config.TextColumn(width="large"),
                                "技术参数": st.column_config.TextColumn(width="medium"),
                                "使用场景": st.column_config.TextColumn(width="medium"),
                                "差异化卖点": st.column_config.TextColumn(width="medium"),
                                "搜索关键词": st.column_config.TextColumn(width="medium"),
                                "包装内容": st.column_config.TextColumn(width="medium"),
                            },
                        )

                    # 失败日志
                    if failed:
                        with st.expander(f"⚠️ Phase 1 萃取失败详情（{len(failed)} 条）", expanded=False):
                            for item in failed:
                                st.text(f"❌ {item['asin']}: {item.get('error', '未知错误')}")

                # ============================================
                # 阶段二：AI 文案生成
                # ============================================
                st.divider()
                st.subheader("✍️ 阶段二：AI 文案生成")

                phase1_completed = st.session_state.phase1_done and st.session_state.phase1_results
                p2_key = st.session_state.p2_api_key or st.session_state.p1_api_key
                p2_disabled = not phase1_completed or not p2_key

                if not phase1_completed:
                    st.caption("⏳ 请先完成「阶段一：AI 信息萃取」")
                elif not p2_key:
                    st.caption("⚠️ 请先在侧边栏配置 Phase 2 API Key（或启用 Phase 1 设置复用）")

                if st.button(
                    "✍️ AI 文案生成",
                    type="primary",
                    use_container_width=True,
                    disabled=p2_disabled,
                    key="phase2_btn",
                ):
                    # 同步配置到 settings
                    if st.session_state.p2_api_key:
                        settings.phase2_api_provider = st.session_state.p2_provider
                        settings.phase2_api_key = st.session_state.p2_api_key
                        settings.phase2_api_base_url = st.session_state.p2_base_url
                        settings.phase2_model = st.session_state.p2_model
                    else:
                        # fallback 到 Phase 1 配置
                        settings.phase2_api_provider = st.session_state.p1_provider
                        settings.phase2_api_key = st.session_state.p1_api_key
                        settings.phase2_api_base_url = st.session_state.p1_base_url
                        settings.phase2_model = st.session_state.p1_model

                    # 获取待处理的 ASIN（只处理 Phase 1 成功的）
                    phase1_success_asins = [
                        r["asin"] for r in st.session_state.phase1_results
                        if "error" not in r
                    ]

                    # 进度区域
                    p2_progress = st.progress(0)
                    p2_status = st.empty()
                    p2_status.info(f"🔄 Phase 2 文案生成中... 共 {len(phase1_success_asins)} 个 ASIN")

                    # 执行 Phase 2
                    results = execute_phase2_generation(
                        asins=phase1_success_asins,
                        db_path=DB_PATH,
                        progress_callback=lambda cur, tot: p2_progress.progress(
                            int(cur / tot * 100) if tot > 0 else 0
                        ),
                    )

                    p2_progress.progress(100)
                    success_count = len([r for r in results if "error" not in r])
                    fail_count = len([r for r in results if "error" in r])
                    p2_status.success(
                        f"✅ Phase 2 文案生成完成！成功 {success_count}/{len(phase1_success_asins)}"
                        + (f"，失败 {fail_count}" if fail_count > 0 else "")
                    )

                    # 生成下载数据
                    download_data = generate_phase2_download(
                        db_path=DB_PATH,
                        asins=[r["asin"] for r in results if "error" not in r],
                    )

                    st.session_state.phase2_done = True
                    st.session_state.phase2_results = results
                    st.session_state.phase2_download_data = download_data
                    st.rerun()

                # Phase 2 结果展示
                if st.session_state.phase2_done and st.session_state.phase2_results:
                    results = st.session_state.phase2_results
                    success = [r for r in results if "error" not in r]
                    failed = [r for r in results if "error" in r]

                    # 读取数据库获取预览数据
                    import sqlite3
                    from db import get_product, get_translation

                    db_path = DB_PATH
                    db = sqlite3.connect(db_path)
                    preview_rows = []
                    for r in success:
                        product = get_product(db, r["asin"])
                        translation = get_translation(db, r["asin"])
                        if product and translation:
                            preview_rows.append({
                                "ASIN": r["asin"],
                                "标题": product.get("title", ""),
                                "核心流量词": translation.get("core_keywords", ""),
                                "俄语标题": translation.get("russian_title", ""),
                                "俄语详情": (translation.get("russian_description", "") or "")[:100] + "...",
                            })
                    db.close()

                    if preview_rows:
                        import pandas as pd
                        st.caption(f"📊 翻译结果预览（{len(preview_rows)} 条）")
                        df = pd.DataFrame(preview_rows)
                        st.dataframe(df, use_container_width=True, hide_index=True)

                    # 失败日志
                    if failed:
                        with st.expander(f"⚠️ Phase 2 生成失败详情（{len(failed)} 条）", expanded=False):
                            for item in failed:
                                st.text(f"❌ {item['asin']}: {item.get('error', '未知错误')}")

                    # 下载按钮
                    if st.session_state.phase2_download_data:
                        st.download_button(
                            label="📥 下载处理后表格（xlsx）",
                            data=st.session_state.phase2_download_data,
                            file_name="爬虫表格（处理后）.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                        )

        finally:
            # 清理临时文件
            try:
                os.unlink(temp_path)
            except OSError:
                pass

# ---------- Tab 3: 图片翻译 ----------

with tab3:
    st.header("功能区 3：图片翻译")

    # --- 管线选择 ---
    pipeline_mode = st.radio(
        "翻译管线",
        options=["traditional", "ai"],
        format_func=lambda x: "📐 传统管线（OCR → 翻译 → 擦除 → 覆写）" if x == "traditional" else "🤖 AI 管线（中转站/Gemini 直接翻译图中文字）",
        horizontal=True,
        key="image_pipeline_radio",
        help="传统管线：OCR检测文字位置 → LLM翻译 → 擦除英文 → Pillow覆写俄文。AI管线：AI视觉模型端到端翻译图片中的文字（需在侧边栏配置 AI 图片 API）。",
    )

    # ═══════════════════════════════════════════
    # 传统管线（OCR + 翻译 + 擦除 + 覆写）
    # ═══════════════════════════════════════════
    if pipeline_mode == "traditional":
        st.caption("传统管线：下载图片 → OCR 检测文字 → Phase1 API 翻译 → 擦除英文 → 覆写俄文 → 3:4 缩放 → R2 上传")

        uploaded_file = st.file_uploader(
            "上传包含 ASIN 和图片URL 列的 Excel 文件",
            type=["xlsx"],
            help="兼容爬虫 4 列输出（asin, 图片url, 标题, 详情）或翻译后 12 列输出",
            key="image_uploader",
        )

        if uploaded_file is None:
            # 文件被清除时，清除旧结果
            if st.session_state.image_results is not None:
                st.session_state.image_results = None
                st.session_state.image_upload_info = None
                st.session_state.image_products = None
                st.session_state.image_font_config = None
                st.session_state.image_original_bytes = None
            # 同时停止轮询
            if st.session_state.image_polling:
                st.session_state.image_polling = False
            st.info("👆 请上传 Excel 文件开始图片翻译")
        else:
            # 保存原始文件字节（用于后续合并下载）
            st.session_state.image_original_bytes = uploaded_file.getvalue()
            # --- 保存上传文件到临时路径 ---
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp.write(uploaded_file.getvalue())
                temp_path = tmp.name

            try:
                # --- 校验 ---
                upload_result: ImageUploadResult = validate_image_upload(temp_path)

                if not upload_result.is_valid:
                    st.error(f"❌ {upload_result.error}")
                else:
                    import re as _re
                    total_images = sum(
                        len([u for u in _re.split(r'[;|]', p.get("图片url", "")) if u.strip()])
                        for p in upload_result.products
                    )
                    st.success(
                        f"✅ 检测到 **{upload_result.count}** 个 ASIN，共 **{total_images}** 张图片"
                        + (f"（格式：翻译后 12 列）" if upload_result.input_type == "translation_output" else "")
                    )

                    # --- 字体配置 ---
                    with st.expander("🔤 字体配置", expanded=False):
                        col_f1, col_f2, col_f3 = st.columns(3)
                        with col_f1:
                            font_name = st.text_input(
                                "字体文件",
                                value="Roboto-Regular.ttf",
                                help="需放在 fonts/ 目录或系统字体目录",
                            )
                        with col_f2:
                            auto_size = st.checkbox("自动字号", value=True, help="根据文字区域自动计算字号")
                        with col_f3:
                            manual_size = st.number_input(
                                "手动字号",
                                min_value=8, max_value=72, value=24,
                                disabled=auto_size,
                            )

                    # --- 检查 API Key（传统管线使用 Phase 1 配置）---
                    img_api_key = st.session_state.p1_api_key or st.session_state.get("p1_api_key", "")
                    if not img_api_key:
                        st.warning("⚠️ 请先在侧边栏「🔍 Phase 1 API」配置 API Key")
                    else:
                        if st.button("🚀 开始图片翻译（传统管线）", type="primary", use_container_width=True):
                            # 同步 session_state 配置到 settings（传统管线使用 Phase 1 配置）
                            settings.translate_api_provider = st.session_state.p1_provider
                            settings.translate_api_key = st.session_state.p1_api_key
                            settings.translate_api_base_url = st.session_state.p1_base_url
                            settings.translate_model = st.session_state.p1_model

                            # --- 从数据库丰富产品上下文（Phase 1/2 产物）---
                            db_path = DB_PATH
                            enriched_products = enrich_product_context_from_db(
                                upload_result.products, db_path
                            )

                            # --- 字体配置 ---
                            font_config = FontConfig(
                                font_name=font_name,
                                auto_size=auto_size,
                                manual_size=manual_size,
                            )

                            # --- 启动后台翻译（非阻塞）---
                            start_background_translation(
                                products=enriched_products,
                                font_config=font_config,
                                progress_file="image_progress.json",
                            )

                            # 存入 session_state（供轮询和后续显示使用）
                            st.session_state.image_polling = True
                            st.session_state.image_progress_file = "image_progress.json"
                            st.session_state.image_products = enriched_products
                            st.session_state.image_font_config = font_config
                            st.session_state.image_original_bytes = uploaded_file.getvalue()
                            st.session_state.image_upload_info = {
                                "count": upload_result.count,
                                "total_images": total_images,
                                "input_type": upload_result.input_type,
                            }
                            st.rerun()

            finally:
                # 清理临时文件
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

        # ── 实时进度轮询（传统管线）──
        if st.session_state.get("image_polling"):
            progress = read_progress(st.session_state.image_progress_file)

            if progress is None:
                st.warning("⚠️ 进度文件丢失，请重新运行翻译。")
                st.session_state.image_polling = False
                st.rerun()
            else:
                state = progress.get("state", "running")
                total = progress.get("total_images", 0)
                processed = progress.get("processed_images", 0)
                current_asin = progress.get("current_asin", "")

                st.divider()
                st.subheader("📊 实时翻译进度（传统管线）")

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("总图片", total)
                with col2:
                    st.metric("已完成", processed)
                with col3:
                    st.metric("当前 ASIN", current_asin or "—")

                if total > 0:
                    st.progress(processed / total)

                # 图片状态表格
                _show_image_progress_table(progress)

                if state == "completed":
                    # 构建结果对象存入 session_state
                    results = _build_image_results_from_progress(progress)
                    st.session_state.image_results = results
                    st.session_state.image_polling = False

                    success = progress.get("success_count", 0)
                    errors = progress.get("error_count", 0)
                    skipped = progress.get("skipped_count", 0)
                    videos = progress.get("video_count", 0)
                    st.success(
                        f"✅ 处理完成！成功 {success} | 跳过 {skipped} | "
                        f"视频 {videos} | 失败 {errors}"
                        + f"（共 {total} 张图片/视频）"
                    )
                    st.rerun()
                else:
                    _time.sleep(30)
                    st.rerun()

        # ── 传统管线持久化结果展示（不受 rerun 影响）──
        if st.session_state.image_results is not None:
            results = st.session_state.image_results
            info = st.session_state.image_upload_info or {}
            st.divider()
            st.subheader("📊 处理结果（传统管线）")

            # 汇总摘要
            total = results.total_images
            st.markdown(
                f"**{info.get('count', '?')}** 个 ASIN，共 **{total}** 张图片/视频 | "
                f"✅ 成功 {results.success_images} | ⏭️ 跳过 {results.skipped_images}"
                f" | 🎬 视频 {results.video_images} | ❌ 失败 {results.error_images}"
            )

            # 构建预览数据
            preview_rows = []
            for asin_result in results.results:
                for img in asin_result.images:
                    preview_rows.append({
                        "ASIN": asin_result.asin,
                        "序号": img.index,
                        "状态": img.status,
                        "R2 URL": img.r2_url[:80] + "..." if len(img.r2_url) > 80 else img.r2_url,
                        "错误": img.error[:50] if img.error else "",
                    })

            import pandas as pd
            df = pd.DataFrame(preview_rows)
            st.dataframe(df, use_container_width=True, hide_index=True)

            # ── 失败/跳过详情（含单图重试 + 一键重试按钮）──
            has_problems = (
                results.error_images > 0 or results.skipped_images > 0
            )
            if has_problems:
                products = st.session_state.get("image_products")
                font_cfg = st.session_state.get("image_font_config")

                # ── 一键重试（在 expander 外面，醒目）──
                if products is not None and font_cfg is not None:
                    failed_count = results.error_images
                    if failed_count > 0 and st.button(
                        f"🔄 一键重新翻译（{failed_count} 张失败图片）",
                        type="secondary",
                        use_container_width=True,
                        key="batch_retry_traditional",
                    ):
                        from image_translator_ui import (
                            _find_product_context,
                            _replace_and_recount,
                            retry_single_image,
                        )

                        db_path = DB_PATH
                        enriched = enrich_product_context_from_db(
                            list(products), db_path
                        )

                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        retried = 0
                        total_failed = failed_count

                        for asin_result in results.results:
                            for img in asin_result.images:
                                if img.status != "error":
                                    continue
                                retried += 1
                                status_text.text(
                                    f"🔄 重试中 {retried}/{total_failed}: "
                                    f"{asin_result.asin} 图{img.index}..."
                                )
                                progress_bar.progress(retried / total_failed)

                                ctx = _find_product_context(
                                    enriched, asin_result.asin
                                )
                                new_result = retry_single_image(
                                    image_url=img.original_url,
                                    asin=asin_result.asin,
                                    index=img.index,
                                    font_config=font_cfg,
                                    product_context=ctx,
                                )
                                _replace_and_recount(
                                    results,
                                    asin_result.asin,
                                    img.index,
                                    new_result,
                                )

                        status_text.success(
                            f"✅ 一键重试完成！成功 {results.success_images} | "
                            f"失败 {results.error_images}"
                        )
                        st.rerun()

                with st.expander(
                    f"⚠️ 失败/跳过详情（{results.error_images + results.skipped_images} 张）",
                    expanded=False,
                ):
                    if products is None or font_cfg is None:
                        st.warning(
                            "原始上传信息已丢失，无法重试。"
                            "请重新上传文件并运行翻译。"
                        )
                    else:
                        from image_translator_ui import (
                            _find_product_context,
                            _replace_and_recount,
                            retry_single_image,
                        )

                        for asin_result in results.results:
                            for img in asin_result.images:
                                if img.status not in ("error", "skipped"):
                                    continue

                                col_info, col_btn = st.columns([4, 1])
                                with col_info:
                                    emoji = "❌" if img.status == "error" else "⏭️"
                                    retry_note = (
                                        f" | 已重试 {img.retry_count} 次"
                                        if img.retry_count > 0
                                        else ""
                                    )
                                    st.caption(
                                        f"{emoji} **{asin_result.asin}**  "
                                        f"图{img.index} | "
                                        f"{img.error[:60] if img.error else '跳过'}"
                                        f"{retry_note}"
                                    )
                                with col_btn:
                                    if st.button(
                                        "🔄 重新翻译",
                                        key=f"retry_{asin_result.asin}_{img.index}",
                                        use_container_width=True,
                                    ):
                                        db_path = DB_PATH
                                        enriched = enrich_product_context_from_db(
                                            list(products), db_path
                                        )
                                        ctx = _find_product_context(
                                            enriched, asin_result.asin
                                        )
                                        new_result = retry_single_image(
                                            image_url=img.original_url,
                                            asin=asin_result.asin,
                                            index=img.index,
                                            font_config=font_cfg,
                                            product_context=ctx,
                                        )
                                        _replace_and_recount(
                                            results,
                                            asin_result.asin,
                                            img.index,
                                            new_result,
                                        )
                                        st.rerun()

            # --- 合并表格下载（将 R2 URL 写回原始 Excel）---
            original_bytes = st.session_state.get("image_original_bytes")
            if original_bytes:
                # 构建 URL 映射：原始 URL → R2 URL（仅成功处理的图片）
                url_mapping: dict[str, str] = {}
                for asin_result in results.results:
                    for img in asin_result.images:
                        if img.r2_url and img.status == "ok":
                            url_mapping[img.original_url] = img.r2_url
                if url_mapping:
                    merged_bytes = generate_merged_excel(original_bytes, url_mapping)
                    st.download_button(
                        label="📥 下载合并表格（图片URL → R2 URL，含翻译状态列）",
                        data=merged_bytes,
                        file_name="处理表格（图片URL已替换）.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )
                else:
                    st.caption("⚠️ 没有成功处理的图片，无法生成合并表格")

            # --- 下载按钮（持久化，点击后不会消失）---
            col_dl1, col_dl2 = st.columns(2)
            with col_dl1:
                xlsx_bytes = generate_output_excel(results)
                st.download_button(
                    label="📥 下载图片结果明细（xlsx）",
                    data=xlsx_bytes,
                    file_name="图片翻译结果.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            with col_dl2:
                report_bytes = generate_report(results)
                st.download_button(
                    label="📋 下载处理报告（CSV）",
                    data=report_bytes,
                    file_name="图片翻译报告.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

            st.info(
                "💡 R2 公开 URL 已生成。\n"
                "**合并表格** = 原始 Excel 的全部行列 + R2图片url列 + 翻译状态列。\n"
                "**明细表格** = 每张图片一行的处理结果。"
            )

    # ═══════════════════════════════════════════
    # AI 管线（中转站 / Gemini 端到端翻译）
    # ═══════════════════════════════════════════
    else:
        st.caption("AI 管线：下载图片 → AI 视觉模型直接翻译图中英文为俄文 → 3:4 缩放 → R2 上传")

        uploaded_file = st.file_uploader(
            "上传包含 ASIN 和图片URL 列的 Excel 文件",
            type=["xlsx"],
            help="兼容爬虫 4 列输出或翻译后 12 列输出。AI 会端到端翻译图片中的英文为俄文。",
            key="image_ai_uploader",
        )

        if uploaded_file is None:
            # 文件被清除时，清除旧 AI 结果
            if st.session_state.image_ai_results is not None:
                st.session_state.image_ai_results = None
                st.session_state.image_ai_products = None
                st.session_state.image_ai_upload_info = None
                st.session_state.image_ai_original_bytes = None
            # 同时停止轮询
            if st.session_state.image_ai_polling:
                st.session_state.image_ai_polling = False
            st.info("👆 请上传 Excel 文件开始 AI 图片翻译")
        else:
            # 保存原始文件字节（用于后续合并下载）
            st.session_state.image_ai_original_bytes = uploaded_file.getvalue()
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp.write(uploaded_file.getvalue())
                temp_path = tmp.name

            try:
                upload_result: ImageUploadResult = validate_image_upload(temp_path)

                if not upload_result.is_valid:
                    st.error(f"❌ {upload_result.error}")
                else:
                    import re as _re
                    total_images = sum(
                        len([u for u in _re.split(r'[;|]', p.get("图片url", "")) if u.strip()])
                        for p in upload_result.products
                    )
                    st.success(
                        f"✅ 检测到 **{upload_result.count}** 个 ASIN，共 **{total_images}** 张图片"
                        + (f"（格式：翻译后 12 列）" if upload_result.input_type == "translation_output" else "")
                    )

                    # --- 检查 AI 图片 API 配置 ---
                    provider = st.session_state.image_gen_provider
                    if provider == "gemini":
                        api_ok = bool(st.session_state.gemini_api_key)
                        api_label = "Gemini API Key"
                    else:
                        api_ok = bool(st.session_state.image_gen_api_key and st.session_state.image_gen_base_url)
                        api_label = "中转站 API Key + Base URL"

                    if not api_ok:
                        st.warning(f"⚠️ 请先在侧边栏「🎨 AI 图片 API」配置 {api_label}")
                    else:
                        if st.button("🚀 开始 AI 图片翻译", type="primary", use_container_width=True):
                            # 同步 AI 图片 API 配置到 settings
                            settings.image_gen_provider = st.session_state.image_gen_provider
                            settings.image_gen_api_key = st.session_state.get("image_gen_api_key", "")
                            settings.image_gen_base_url = st.session_state.get("image_gen_base_url", "")
                            settings.image_gen_model = st.session_state.get("image_gen_model", "")
                            settings.gemini_api_key = st.session_state.get("gemini_api_key", "")
                            settings.gemini_model = st.session_state.get("gemini_model", "gemini-2.5-flash-image")
                            settings.gemini_proxy = st.session_state.get("gemini_proxy", "")

                            provider_label = "中转站" if provider == "openai_compatible" else "Gemini"

                            db_path = DB_PATH
                            enriched_products = enrich_product_context_from_db(
                                upload_result.products, db_path
                            )

                            # --- 启动后台 AI 翻译（非阻塞）---
                            start_background_card_generation(
                                products=enriched_products,
                                mode="translate",
                                progress_file="card_image_progress.json",
                            )

                            st.session_state.image_ai_polling = True
                            st.session_state.image_ai_progress_file = "card_image_progress.json"
                            st.session_state.image_ai_products = enriched_products
                            st.session_state.image_ai_original_bytes = uploaded_file.getvalue()
                            st.session_state.image_ai_upload_info = {
                                "count": upload_result.count,
                                "total_images": total_images,
                                "input_type": upload_result.input_type,
                                "provider": provider_label,
                            }
                            st.rerun()

            finally:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

        # ── 实时进度轮询（AI 管线）──
        if st.session_state.get("image_ai_polling"):
            progress = read_progress(st.session_state.image_ai_progress_file)

            if progress is None:
                st.warning("⚠️ 进度文件丢失，请重新运行翻译。")
                st.session_state.image_ai_polling = False
                st.rerun()
            else:
                state = progress.get("state", "running")
                total = progress.get("total_images", 0)
                processed = progress.get("processed_images", 0)
                current_asin = progress.get("current_asin", "")
                info = st.session_state.image_ai_upload_info or {}
                provider_label = info.get("provider", "AI")

                st.divider()
                st.subheader(f"📊 实时翻译进度（{provider_label}）")

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("总图片", total)
                with col2:
                    st.metric("已完成", processed)
                with col3:
                    st.metric("当前 ASIN", current_asin or "—")

                if total > 0:
                    st.progress(processed / total)

                # 图片状态表格
                _show_image_progress_table(progress)

                if state == "completed":
                    # 构建结果对象存入 session_state
                    results = _build_card_results_from_progress(progress)
                    st.session_state.image_ai_results = results
                    st.session_state.image_ai_polling = False

                    success = progress.get("success_count", 0)
                    errors = progress.get("error_count", 0)
                    skipped = progress.get("skipped_count", 0)
                    videos = progress.get("video_count", 0)
                    st.success(
                        f"✅ 处理完成！成功 {success} | 跳过 {skipped} | "
                        f"视频 {videos} | 失败 {errors}"
                        + f"（共 {total} 张图片/视频）"
                    )
                    st.rerun()
                else:
                    _time.sleep(30)
                    st.rerun()

        # ── AI 管线持久化结果展示 ──
        if st.session_state.image_ai_results is not None:
            results = st.session_state.image_ai_results
            info = st.session_state.image_ai_upload_info or {}
            st.divider()
            st.subheader(f"📊 AI 翻译结果（{info.get('provider', 'AI')}）")

            total = results.total_cards
            st.markdown(
                f"**{info.get('count', '?')}** 个 ASIN，共 **{total}** 张图片/视频 | "
                f"✅ 成功 {results.success_cards} | ⏭️ 跳过 {results.skipped_cards}"
                f" | 🎬 视频 {results.video_cards} | ❌ 失败 {results.error_cards}"
            )

            preview_rows = []
            for asin_result in results.results:
                for card in asin_result.cards:
                    preview_rows.append({
                        "ASIN": asin_result.asin,
                        "序号": card.index,
                        "状态": card.status,
                        "R2 URL": card.r2_url[:80] + "..." if len(card.r2_url) > 80 else card.r2_url,
                        "设计说明": card.design_description[:60] if card.design_description else "",
                        "错误": card.error[:50] if card.error else "",
                    })

            import pandas as pd
            df = pd.DataFrame(preview_rows)
            st.dataframe(df, use_container_width=True, hide_index=True)

            # ── 失败详情 + 重试（含一键重试按钮）──
            has_problems = results.error_cards > 0 or results.skipped_cards > 0
            if has_problems:
                products = st.session_state.get("image_ai_products")

                # ── 一键重试（在 expander 外面，醒目）──
                if products is not None:
                    failed_count = results.error_cards
                    if failed_count > 0 and st.button(
                        f"🔄 一键重新翻译（{failed_count} 张失败图片）",
                        type="secondary",
                        use_container_width=True,
                        key="batch_retry_ai",
                    ):
                        from image_generator_ui import (
                            _find_product_context,
                            _replace_and_recount_cards,
                            retry_single_card as retry_single_card_gen,
                        )

                        db_path = DB_PATH
                        enriched = enrich_product_context_from_db(
                            list(products), db_path
                        )

                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        retried = 0
                        total_failed = failed_count

                        for asin_result in results.results:
                            for card in asin_result.cards:
                                if card.status != "error":
                                    continue
                                retried += 1
                                status_text.text(
                                    f"🔄 重试中 {retried}/{total_failed}: "
                                    f"{asin_result.asin} 图{card.index}..."
                                )
                                progress_bar.progress(retried / total_failed)

                                ctx = _find_product_context(
                                    enriched, asin_result.asin
                                )
                                new_result = retry_single_card_gen(
                                    image_url=card.original_url,
                                    asin=asin_result.asin,
                                    index=card.index,
                                    product_context=ctx,
                                    mode="translate",
                                )
                                _replace_and_recount_cards(
                                    results,
                                    asin_result.asin,
                                    card.index,
                                    new_result,
                                )

                        status_text.success(
                            f"✅ 一键重试完成！成功 {results.success_cards} | "
                            f"失败 {results.error_cards}"
                        )
                        st.rerun()

                with st.expander(
                    f"⚠️ 失败/跳过详情（{results.error_cards + results.skipped_cards} 张）",
                    expanded=False,
                ):
                    if products is None:
                        st.warning("原始上传信息已丢失，请重新上传文件并运行翻译。")
                    else:
                        from image_generator_ui import (
                            _find_product_context,
                            _replace_and_recount_cards,
                            retry_single_card as retry_single_card_gen,
                        )

                        for asin_result in results.results:
                            for card in asin_result.cards:
                                if card.status not in ("error", "skipped"):
                                    continue

                                col_info, col_btn = st.columns([4, 1])
                                with col_info:
                                    emoji = "❌" if card.status == "error" else "⏭️"
                                    retry_note = (
                                        f" | 已重试 {card.retry_count} 次"
                                        if card.retry_count > 0
                                        else ""
                                    )
                                    st.caption(
                                        f"{emoji} **{asin_result.asin}**  "
                                        f"图{card.index} | "
                                        f"{card.error[:60] if card.error else '跳过'}"
                                        f"{retry_note}"
                                    )
                                with col_btn:
                                    if st.button(
                                        "🔄 重新翻译",
                                        key=f"ai_retry_{asin_result.asin}_{card.index}",
                                        use_container_width=True,
                                    ):
                                        db_path = DB_PATH
                                        enriched = enrich_product_context_from_db(
                                            list(products), db_path
                                        )
                                        ctx = _find_product_context(
                                            enriched, asin_result.asin
                                        )
                                        new_result = retry_single_card_gen(
                                            image_url=card.original_url,
                                            asin=asin_result.asin,
                                            index=card.index,
                                            product_context=ctx,
                                            mode="translate",
                                        )
                                        _replace_and_recount_cards(
                                            results,
                                            asin_result.asin,
                                            card.index,
                                            new_result,
                                        )
                                        st.rerun()

            # --- 合并表格下载（将 R2 URL 写回原始 Excel）---
            original_bytes = st.session_state.get("image_ai_original_bytes")
            if original_bytes:
                # 构建 URL 映射
                url_mapping: dict[str, str] = {}
                for asin_result in results.results:
                    for card in asin_result.cards:
                        if card.r2_url and card.status == "ok":
                            url_mapping[card.original_url] = card.r2_url
                if url_mapping:
                    merged_bytes = generate_merged_excel(original_bytes, url_mapping)
                    st.download_button(
                        label="📥 下载合并表格（图片URL → R2 URL，含翻译状态列）",
                        data=merged_bytes,
                        file_name="AI处理表格（图片URL已替换）.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )
                else:
                    st.caption("⚠️ 没有成功处理的图片，无法生成合并表格")

            # --- 下载按钮 ---
            col_dl1, col_dl2 = st.columns(2)
            with col_dl1:
                from image_generator_ui import generate_card_output_excel
                xlsx_bytes = generate_card_output_excel(results)
                st.download_button(
                    label="📥 下载 AI 翻译明细（xlsx）",
                    data=xlsx_bytes,
                    file_name="AI图片翻译结果.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            with col_dl2:
                from image_generator_ui import generate_card_report
                report_bytes = generate_card_report(results)
                st.download_button(
                    label="📋 下载 AI 翻译报告（CSV）",
                    data=report_bytes,
                    file_name="AI图片翻译报告.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

            st.info(
                "💡 AI 翻译由视觉模型端到端完成，保留原产品外观但替换文字为俄文。\n"
                "**合并表格** = 原始 Excel 的全部行列 + R2图片url列 + 翻译状态列。\n"
                "**明细表格** = 每张图片一行的处理结果。"
            )

# ---------- Tab 4: 产品卡片设计 ----------

with tab4:
    st.header("功能区 4：产品卡片设计")

    st.markdown(
        "使用 **AI 视觉模型**（Gemini 或中转站）直接生成俄罗斯电商风格的产品卡片图。"
        "直接上传产品白底图，AI 将根据设计大师人设自动生成"
        "符合 Wildberries/Ozon 审美的 3:4 产品主图。"
        "\n\n在侧边栏「🎨 AI 图片 API」中选择提供商（Gemini / 中转站）并配置 API Key。"
    )

    # ── 上传图片 ──
    uploaded_images = st.file_uploader(
        "上传产品白底图（可多选）",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key="card_gen_image_upload",
        help="直接上传产品图片，AI 将自动生成 Wildberries 风格的产品卡片。",
    )

    # 文件被清除时，清除旧结果
    if not uploaded_images and st.session_state.get("card_gen_results") is not None:
        st.session_state.card_gen_results = None
        st.session_state.card_gen_uploaded_images = None

    # ── 自定义提示词（始终可见）──
    st.subheader("💬 自定义提示词（可选）")
    st.caption("补充设计要求，会附加到 AI 生成 prompt 末尾。例如：背景用深色、添加价格标签、突出产品材质等。")
    custom_prompt = st.text_area(
        "提示词",
        value=st.session_state.card_gen_custom_prompt,
        height=100,
        placeholder="输入额外的设计要求...",
        label_visibility="collapsed",
        key="card_gen_custom_prompt_input",
    )
    st.session_state.card_gen_custom_prompt = custom_prompt

    if not uploaded_images:
        st.info("👆 请上传产品图片开始", icon=":material/upload:")
    else:
        st.success(f"✅ 已选择 **{len(uploaded_images)}** 张图片")

        # 显示缩略图预览
        with st.expander("🖼️ 图片预览", expanded=False):
            cols = st.columns(min(len(uploaded_images), 5))
            for i, img_file in enumerate(uploaded_images[:10]):
                with cols[i % 5]:
                    st.image(img_file, caption=img_file.name[:30], use_container_width=True)
            if len(uploaded_images) > 10:
                st.caption(f"... 还有 {len(uploaded_images) - 10} 张未显示")

        # ── 开始生成按钮 ──
        if st.button("🎨 开始卡片设计", type="primary", use_container_width=True):
            # 同步配置到 settings
            settings.image_gen_provider = st.session_state.image_gen_provider
            settings.image_gen_api_key = st.session_state.get("image_gen_api_key", "")
            settings.image_gen_base_url = st.session_state.get("image_gen_base_url", "")
            settings.image_gen_model = st.session_state.get("image_gen_model", "")
            settings.gemini_api_key = st.session_state.get("gemini_api_key", "")
            settings.gemini_model = st.session_state.get("gemini_model", "gemini-2.5-flash-image")
            settings.gemini_proxy = st.session_state.get("gemini_proxy", "")

            # 校验必要配置
            config_error = None
            if settings.image_gen_provider == "gemini":
                if not settings.gemini_api_key:
                    config_error = "❌ 请先在侧边栏配置 Gemini API Key"
            elif settings.image_gen_provider == "openai_compatible":
                if not settings.image_gen_api_key:
                    config_error = "❌ 请先在侧边栏配置 API Key"
                elif not settings.image_gen_base_url:
                    config_error = "❌ 请先在侧边栏配置接口地址（Base URL）"

            if config_error:
                st.error(config_error)
            else:
                provider_label = "Gemini" if settings.image_gen_provider == "gemini" else "中转站"
                with st.spinner(f"🎨 {provider_label} 正在生成产品卡片（这可能需要几分钟）..."):
                    try:
                        results = run_card_generation_from_images(
                            uploaded_files=list(uploaded_images),
                            custom_prompt=st.session_state.card_gen_custom_prompt,
                        )
                        st.session_state.card_gen_results = results
                        # 保存上传图片的字节数据（供重试用）
                        st.session_state.card_gen_uploaded_images = {
                            img_file.name: img_file.getvalue()
                            for img_file in uploaded_images
                        }
                    except Exception as e:
                        st.error(f"❌ 生成过程出错: {e}")
                        import traceback
                        with st.expander("🔍 详细错误"):
                            st.code(traceback.format_exc())

        # ── 显示结果 ──
        if "card_gen_results" in st.session_state and st.session_state.card_gen_results is not None:
            results = st.session_state.card_gen_results

            st.divider()
            st.subheader("📊 生成结果")

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("总图片", results.total_cards)
            with col2:
                st.metric("成功", results.success_cards)
            with col3:
                st.metric("失败", results.error_cards)

            # ── 结果详情表 ──
            st.subheader("📋 详情")
            report_rows = []
            for asin_result in results.results:
                for card in asin_result.cards:
                    status_icon = {
                        "ok": "✅",
                        "error": "❌",
                        "skipped": "⏭️",
                        "video": "🎬",
                    }.get(card.status, "❓")

                    report_rows.append({
                        "文件名": asin_result.asin,
                        "状态": f"{status_icon} {card.status}",
                        "R2 URL": card.r2_url[:80] + "..." if len(card.r2_url) > 80 else card.r2_url,
                        "设计说明": card.design_description[:100] + "..." if len(card.design_description) > 100 else card.design_description,
                        "错误": card.error[:80] if card.error else "",
                    })
            st.dataframe(report_rows, use_container_width=True)

            # ── 单张重试 ──
            error_cards = [
                (asin_result, card)
                for asin_result in results.results
                for card in asin_result.cards
                if card.status == "error"
            ]
            if error_cards:
                st.subheader(f"🔄 重试失败卡片（{len(error_cards)} 张）")
                saved_images = st.session_state.get("card_gen_uploaded_images", {})

                for asin_result, card in error_cards[:10]:
                    with st.container():
                        col_a, col_b = st.columns([3, 1])
                        with col_a:
                            st.text(
                                f"文件: {asin_result.asin} | "
                                f"错误: {card.error[:80]}"
                            )
                        with col_b:
                            # 从 ASIN 中提取原始文件名来匹配保存的图片
                            if st.button(
                                "🔄 重新生成",
                                key=f"card_retry_{asin_result.asin}_{card.index}",
                                use_container_width=True,
                            ):
                                # 尝试从保存的图片中找到对应的原始文件
                                retry_bytes = None
                                retry_filename = asin_result.asin
                                for orig_name, img_bytes in saved_images.items():
                                    # 用文件名的一部分匹配
                                    import re as _re
                                    clean_orig = _re.sub(r'[^A-Za-z0-9_-]', '_', orig_name[:30])
                                    if clean_orig in asin_result.asin:
                                        retry_bytes = img_bytes
                                        retry_filename = orig_name
                                        break

                                if retry_bytes:
                                    import tempfile
                                    import os as _os
                                    suffix = _os.path.splitext(retry_filename)[1] or ".jpg"
                                    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                                        tmp.write(retry_bytes)
                                        tmp_path = tmp.name

                                    try:
                                        from image_generator_ui import retry_single_card_from_local
                                        new_result = retry_single_card_from_local(
                                            local_path=tmp_path,
                                            filename=retry_filename,
                                            custom_prompt=st.session_state.get("card_gen_custom_prompt", ""),
                                        )
                                    finally:
                                        try:
                                            _os.unlink(tmp_path)
                                        except OSError:
                                            pass

                                    _replace_and_recount_cards(
                                        results,
                                        asin_result.asin,
                                        card.index,
                                        new_result,
                                    )
                                    st.rerun()
                                else:
                                    st.error("找不到原始图片，请重新上传")

            # ── 下载按钮 ──
            col_dl1, col_dl2 = st.columns(2)
            with col_dl1:
                xlsx_bytes = generate_card_output_excel(results)
                st.download_button(
                    label="📥 下载生成结果（xlsx）",
                    data=xlsx_bytes,
                    file_name="AI图片生成结果.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            with col_dl2:
                report_bytes = generate_card_report(results)
                st.download_button(
                    label="📋 下载生成报告（CSV）",
                    data=report_bytes,
                    file_name="AI图片生成报告.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

            st.info(
                "💡 每张卡片由 AI 独立生成，设计风格保持一致但细节可能略有差异。"
                "生成图片已上传至 R2 对象存储，关闭页面后仍可访问。"
            )

# ---------- Tab 5: 工作流 ----------

with tab5:
    render_workflow_tab(DB_PATH, html_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "html"))
