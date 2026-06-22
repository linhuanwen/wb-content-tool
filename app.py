"""
WB Content Tool — Streamlit Web 界面。

功能 Tab：
  - 爬虫采集：上传 ASIN 列表 → 采集亚马逊产品 → 预览 + 下载
  - 文案翻译：上传处理前表格 → AI 翻译 → 下载处理后表格
  - 图片翻译：上传 Excel → 图片下载/OCR/翻译/覆写 → R2 上传 + 下载
"""

import asyncio
import os
import tempfile

import streamlit as st

from config import settings
from crawler_ui import CrawlResult, UploadResult, execute_crawl, generate_download, validate_upload
from image_processor import FontConfig
from image_translator_ui import (
    ImageUploadResult,
    generate_output_excel,
    generate_report,
    run_image_translation,
    validate_image_upload,
)
from translator_ui import (
    TranslationResult,
    TranslationUploadResult,
    execute_translation,
    generate_translation_download,
    validate_translation_upload,
)

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

# 初始化持久化 session_state（防止下载按钮点击后 rerun 导致结果消失）
if "image_results" not in st.session_state:
    st.session_state.image_results = None
if "image_upload_info" not in st.session_state:
    st.session_state.image_upload_info = None

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

    # --- 翻译 API 配置 ---

    st.header("🤖 翻译 API 配置")

    # 初始化 session_state 中的 API 配置（首次加载时从 settings 读取）
    if "api_provider" not in st.session_state:
        st.session_state.api_provider = settings.translate_api_provider
    if "api_key" not in st.session_state:
        st.session_state.api_key = settings.translate_api_key
    if "api_base_url" not in st.session_state:
        st.session_state.api_base_url = settings.translate_api_base_url
    if "api_model" not in st.session_state:
        st.session_state.api_model = settings.translate_model

    provider = st.selectbox(
        "AI 服务商",
        options=["openai", "anthropic", "deepseek", "custom"],
        index=["openai", "anthropic", "deepseek", "custom"].index(
            st.session_state.api_provider
        ) if st.session_state.api_provider in ["openai", "anthropic", "deepseek", "custom"] else 0,
        key="api_provider_select",
    )
    st.session_state.api_provider = provider

    api_key = st.text_input(
        "API Key",
        value=st.session_state.api_key,
        type="password",
        placeholder="sk-...",
    )
    st.session_state.api_key = api_key

    api_base_url = st.text_input(
        "API Base URL",
        value=st.session_state.api_base_url,
        placeholder="留空使用默认地址；自定义服务商时必填",
    )
    st.session_state.api_base_url = api_base_url

    api_model = st.text_input(
        "模型名称",
        value=st.session_state.api_model,
        placeholder="gpt-4o / claude-sonnet-4-6 / deepseek-chat",
    )
    st.session_state.api_model = api_model

    col_save1, col_save2 = st.columns(2)
    with col_save1:
        if st.button("💾 保存配置", use_container_width=True):
            settings.save_to_env(
                provider=provider,
                api_key=api_key,
                base_url=api_base_url,
                model=api_model,
                crawler_mode=st.session_state.crawler_mode,
                scraperapi_key=st.session_state.get("scraperapi_key", ""),
            )
            st.success("配置已保存到 .env 文件 ✅")
    with col_save2:
        if st.button("🔄 重新加载", use_container_width=True):
            st.session_state.api_provider = settings.translate_api_provider
            st.session_state.api_key = settings.translate_api_key
            st.session_state.api_base_url = settings.translate_api_base_url
            st.session_state.api_model = settings.translate_model
            st.rerun()

    st.divider()
    st.caption('配置修改后点击"保存配置"生效')

    st.divider()

    # --- R2 图片存储状态 ---
    st.header("☁️ R2 图片存储")
    if settings.r2_access_key_id:
        st.success(f"✅ R2 已配置 — Bucket: `{settings.r2_bucket}`")
        st.caption(f"域名: {settings.r2_public_domain[:50]}...")
    else:
        st.warning("⚠️ R2 未配置，图片将保存到本地")

# ============================================================
# 主区域 — Tab 布局
# ============================================================

tab1, tab2, tab3 = st.tabs(["🕷️ 爬虫采集", "📝 文案翻译", "🖼️ 图片翻译"])

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

# ---------- Tab 2: 文案翻译 ----------

with tab2:
    st.header("功能区 2：文案翻译")

    uploaded_file = st.file_uploader(
        '上传「处理前」Excel 文件（包含 asin, 图片url, 标题, 详情 四列）',
        type=["xlsx"],
        help='上传爬虫采集生成的「处理前」表格，或符合四列格式的 Excel 文件',
    )

    if uploaded_file is None:
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
                st.success(f"✅ 检测到 **{upload_result.count}** 个待翻译产品")

                # --- 检查 API Key ---
                if not st.session_state.api_key:
                    st.warning("⚠️ 请先在侧边栏配置翻译 API Key，然后再开始翻译")
                else:
                    if st.button("🚀 开始翻译", type="primary", use_container_width=True):
                        # --- 翻译进度区域 ---
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        log_container = st.empty()

                        # 将 session_state 的配置同步到 settings（本次运行中使用）
                        settings.translate_api_provider = st.session_state.api_provider
                        settings.translate_api_key = st.session_state.api_key
                        settings.translate_api_base_url = st.session_state.api_base_url
                        settings.translate_model = st.session_state.api_model

                        status_text.info("🔄 翻译中，请耐心等待...")

                        # --- 执行翻译 ---
                        translation_result: TranslationResult = execute_translation(
                            upload_result.products,
                            progress_callback=lambda current, total: progress_bar.progress(
                                int(current / total * 100) if total > 0 else 0
                            ),
                        )

                        progress_bar.progress(100)
                        total = upload_result.count
                        success_count = len(translation_result.translated)
                        fail_count = len(translation_result.failed)
                        status_text.success(
                            f"✅ 翻译完成！成功 {success_count}/{total}"
                            + (f"，失败 {fail_count}" if fail_count > 0 else "")
                        )

                        # --- 失败警告 ---
                        if translation_result.failed:
                            with st.expander(f"⚠️ 翻译失败详情（{fail_count} 条）", expanded=True):
                                for item in translation_result.failed:
                                    st.text(f"❌ {item['asin']}: {item.get('error', '未知错误')}")

                        # --- 结果预览 ---
                        if translation_result.translated:
                            import pandas as pd
                            df = pd.DataFrame(translation_result.translated)
                            st.subheader(f"📊 翻译结果预览（{len(df)} 条）")
                            # 只展示关键列（前 7 列有实际内容）
                            display_cols = ["asin", "标题", "核心流量词", "俄语标题", "俄语详情"]
                            available_cols = [c for c in display_cols if c in df.columns]
                            st.dataframe(df[available_cols], use_container_width=True, hide_index=True)

                            # --- 下载按钮 ---
                            xlsx_data = generate_translation_download(translation_result.translated)
                            st.download_button(
                                label="📥 下载处理后表格（xlsx）",
                                data=xlsx_data,
                                file_name="爬虫表格（处理后）.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True,
                            )
                        else:
                            st.error("没有成功翻译任何产品，请检查 API 配置或稍后重试。")

        finally:
            # 清理临时文件
            try:
                os.unlink(temp_path)
            except OSError:
                pass

# ---------- Tab 3: 图片翻译 ----------

with tab3:
    st.header("功能区 3：图片翻译")
    st.caption("上传产品 Excel → 下载图片 → OCR 识别 → AI 翻译俄文 → 覆写 → R2 上传")

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
        st.info("👆 请上传 Excel 文件开始图片翻译")
    else:
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

                # --- 检查 API Key ---
                if not st.session_state.api_key:
                    st.warning("⚠️ 请先在侧边栏配置翻译 API Key")
                else:
                    if st.button("🚀 开始图片翻译", type="primary", use_container_width=True):
                        # 同步 session_state 配置到 settings
                        settings.translate_api_provider = st.session_state.api_provider
                        settings.translate_api_key = st.session_state.api_key
                        settings.translate_api_base_url = st.session_state.api_base_url
                        settings.translate_model = st.session_state.api_model

                        # --- 进度区域 ---
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        status_text.info(f"🔄 处理中... 共 {upload_result.count} 个 ASIN")

                        # --- 执行图片翻译 ---
                        font_config = FontConfig(
                            font_name=font_name,
                            auto_size=auto_size,
                            manual_size=manual_size,
                        )

                        results = run_image_translation(
                            products=upload_result.products,
                            font_config=font_config,
                        )

                        # 存入 session_state，防止下载按钮点击后 rerun 导致结果消失
                        st.session_state.image_results = results
                        st.session_state.image_upload_info = {
                            "count": upload_result.count,
                            "total_images": total_images,
                            "input_type": upload_result.input_type,
                        }

                        progress_bar.progress(100)
                        success = results.success_images
                        errors = results.error_images
                        skipped = results.skipped_images
                        status_text.success(
                            f"✅ 处理完成！成功 {success} | 跳过 {skipped} | 失败 {errors}"
                            + f"（共 {results.total_images} 张图片）"
                        )
                        st.rerun()

        finally:
            # 清理临时文件
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    # ── 持久化结果展示（不受 rerun 影响）──
    if st.session_state.image_results is not None:
        results = st.session_state.image_results
        info = st.session_state.image_upload_info or {}
        st.divider()
        st.subheader("📊 处理结果")

        # 汇总摘要
        total = results.total_images
        st.markdown(
            f"**{info.get('count', '?')}** 个 ASIN，共 **{total}** 张图片 | "
            f"✅ 成功 {results.success_images} | ⏭️ 跳过 {results.skipped_images} | ❌ 失败 {results.error_images}"
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

        # --- 下载按钮（持久化，点击后不会消失）---
        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            xlsx_bytes = generate_output_excel(results)
            st.download_button(
                label="📥 下载处理结果（xlsx）",
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

        # --- R2 URL 可访问提示 ---
        st.info(
            "💡 R2 公开 URL 已生成。点击上方下载按钮获取含 R2 URL 的 Excel。\n"
            "关闭本页面后，图片文件仍保留在 R2 对象存储中。"
        )
