"""
Gemini AI 图片生成管道 — 4 步管线（下载→Gemini生成→缩放→上传）。

与 image_translator.py 的 OCR+翻译+覆写 管线并存，
用户可按批次在 UI 中选择使用哪种模式。

公共接口：
    CardResult       — 单张产品卡片生成结果
    AsinCardResult   — 单 ASIN 处理结果
    BatchCardResult  — 批量处理结果
    generate_product_card — 单图完整管线
    generate_asin_cards   — 单 ASIN 并发处理
    generate_batch_cards  — 批量处理 + 断点续跑
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field

from PIL import Image

from image_processor import resize_to_3x4


# ═══════════════════════════════════════════════════════════
# 人设加载
# ═══════════════════════════════════════════════════════════


def _load_gemini_card_persona() -> str:
    """加载 Gemini 产品卡片设计大师人设（system_instruction）。"""
    persona_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "prompts",
        "gemini_card_design_persona.txt",
    )
    if os.path.isfile(persona_path):
        with open(persona_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


# ═══════════════════════════════════════════════════════════
# Prompt 构建
# ═══════════════════════════════════════════════════════════


def _extract_features(product_context: dict[str, str]) -> list[str]:
    """从产品上下文中提取 3 个核心功能点。

    优先从俄语详情中提取短句作为功能点；
    若无俄语详情，则从英文 features/product_category 中抽取。

    Args:
        product_context: 产品数据信息库。

    Returns:
        最多 3 个功能点字符串列表。
    """
    features: list[str] = []

    # 优先：从 features 字段提取（Phase 1 产物）
    raw_features = product_context.get("features", "")
    if raw_features:
        if isinstance(raw_features, str) and "," in raw_features:
            features = [f.strip() for f in raw_features.split(",") if f.strip()]
        elif isinstance(raw_features, str):
            features = [raw_features.strip()]

    # 补充：从俄语详情中提取短句
    if len(features) < 3:
        russian_desc = product_context.get("俄语详情", "")
        if russian_desc:
            # 按句号/换行拆分，取前几个短句
            import re
            sentences = re.split(r"[。\n]", russian_desc)
            for s in sentences:
                s = s.strip()
                if s and len(s) > 5 and s not in features:
                    features.append(s)
                if len(features) >= 3:
                    break

    # 最终兜底
    if not features:
        category = product_context.get("product_category", "")
        material = product_context.get("material", "")
        if category:
            features.append(category)
        if material:
            features.append(material)

    return features[:3]


def _build_design_prompt(
    title: str,
    subtitle: str,
    features: list[str],
    product_category: str = "",
) -> str:
    """构建 Gemini 任务 Prompt（俄语文案 + 设计指令）。

    Args:
        title: 俄语标题。
        subtitle: 俄语副标题（通常为核心流量词）。
        features: 3 个核心功能点（俄语）。
        product_category: 产品类别（用于背景建议）。

    Returns:
        格式化的任务 Prompt 字符串。
    """
    parts: list[str] = []

    parts.append("=== 产品卡片设计任务 ===")
    parts.append("")
    parts.append("请为以下产品设计一张用于 Wildberries/Ozon 平台的主图卡片。")

    # 俄语文案内容
    parts.append("")
    parts.append("【俄语标题】")
    parts.append(title if title else "（俄语标题待补充）")

    parts.append("")
    parts.append("【副标题】")
    parts.append(subtitle if subtitle else "（副标题待补充）")

    if features:
        parts.append("")
        parts.append("【三个核心功能点】")
        for i, feat in enumerate(features, 1):
            parts.append(f"{i}. {feat}")

    if product_category:
        parts.append("")
        parts.append(f"【产品类别】{product_category}")
        parts.append(f"请根据此类别构思符合逻辑的场景背景。")

    # 设计输出要求
    parts.append("")
    parts.append("=== 输出要求 ===")
    parts.append("1. 先用俄语简要说明你将采用的背景方案和色调设计。")
    parts.append("2. 然后生成一张 3:4 比例的高分辨率产品卡片图。")
    parts.append("3. 图片中必须包含：俄语标题（最大）、副标题（中等）、三个功能点（附带小图标）。")
    parts.append("4. 产品主体居中，约占画面 1/3。")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════
# 真实管线实现
# ═══════════════════════════════════════════════════════════

# 复用 image_translator 的下载和上传函数
from image_translator import _real_download, _real_upload, _real_ocr  # noqa: E402


async def _image_has_text(local_path: str) -> bool:
    """检测图片中是否包含可识别文字（用于 translate 模式跳过无文字图片）。

    使用 EasyOCR 检测英文文本。若 EasyOCR 未安装、文件不存在或检测异常，
    则保守处理：认为有文字，走 AI 翻译。
    """
    import os as _os

    if not _os.path.isfile(local_path):
        return True  # 文件不存在，保守走 AI

    try:
        import easyocr  # noqa: F401
    except ImportError:
        return True  # 未安装 OCR，保守走 AI

    try:
        regions = await _real_ocr(local_path)
        return len(regions) > 0
    except Exception:
        return True  # OCR 异常，保守走 AI


async def _verify_translation_output(image_bytes: bytes) -> tuple[bool, list[str]]:
    """核查 AI 翻译后的图片是否仍有未翻译的英文。

    对输出图跑 EasyOCR 英文检测，若检测到显著英文残留（排除短品牌名/型号），
    则认为翻译未完成。

    Args:
        image_bytes: AI 生成/翻译后的图片字节。

    Returns:
        (passed, english_texts) — passed 为 True 表示核查通过（无显著英文残留）；
        english_texts 为检测到的英文文本列表（用于日志/错误信息）。
    """
    import re as _re
    import tempfile
    import os as _os

    # 保存临时文件供 OCR 读取
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = tmp.name

        regions = await _real_ocr(tmp_path)
    except Exception:
        return True, []  # OCR 失败保守通过
    finally:
        if tmp_path and _os.path.isfile(tmp_path):
            try:
                _os.unlink(tmp_path)
            except OSError:
                pass

    if not regions:
        # 输出图中无文字 → 可能是纯背景图，通过
        return True, []

    # 收集所有检测到的文本
    all_texts = [r.get("text", "") for r in regions if r.get("text", "").strip()]

    # 判定「英文残留」：
    #   - 含 >=2 个英文字母
    #   - 不含西里尔字母（说明该区域未被翻译）
    #   - 长度 > 3（排除短品牌名/型号如 "ABC", "X1"）
    english_texts: list[str] = []
    for text in all_texts:
        text = text.strip()
        has_cyrillic = bool(_re.search(r'[А-Яа-яЁё]', text))
        latin_chars = _re.findall(r'[a-zA-Z]', text)
        if not has_cyrillic and len(latin_chars) >= 2 and len(text) > 3:
            english_texts.append(text)

    # 若英文残留文本占比 > 30%，认为翻译未完成
    total_regions = len(all_texts)
    english_region_count = len(english_texts)

    if total_regions > 0 and english_region_count / total_regions > 0.3:
        return False, english_texts

    # 即使占比不高，若英文残留超过 3 个区域也认为有问题
    if english_region_count > 3:
        return False, english_texts

    return True, english_texts


async def _real_gemini_generate(
    image_path: str,
    product_context: dict[str, str] | None = None,
    *,
    mode: str = "card_design",
    custom_prompt: str = "",
) -> tuple[bytes | None, str]:
    """调用 Gemini 2.5 Flash Image API 生成产品卡片图。

    Args:
        image_path: 下载到本地的产品白底图路径。
        product_context: 产品数据信息库（含俄语标题/副标题/功能点等）。
        mode: "card_design"（卡片设计）或 "translate"（图片翻译）。

    Returns:
        (image_bytes, design_description) — 生成的图片字节 + 设计说明。
        若生成失败，image_bytes 为 None。
    """
    from config import settings

    if not settings.gemini_api_key:
        return None, "错误: 未配置 GEMINI_API_KEY"

    product_context = product_context or {}

    # 构建 prompt：translate 模式使用简单翻译 prompt
    if mode == "translate":
        user_prompt = (
            "将图中的英文文字翻译为地道、自然的俄语，"
            "用于俄罗斯电商平台（Wildberries/Ozon）的商品展示。\n"
            "翻译要求：\n"
            "- 使用俄罗斯买家熟悉的电商用语，不要生硬直译\n"
            "- 保留品牌名和型号编号不翻译\n"
            "- 计量单位转换为俄罗斯通用的公制单位\n"
            "- 文字排版与原文风格保持一致，清晰可读\n"
            "- 所有文字必须使用正确的西里尔字母拼写"
        )
        persona = ""
    else:
        persona = _load_gemini_card_persona()
        title = product_context.get("俄语标题", "")
        subtitle = product_context.get("核心流量词", "")
        features = _extract_features(product_context)
        category = product_context.get("product_category", "")
        user_prompt = _build_design_prompt(title, subtitle, features, category)

    # 追加用户自定义提示词
    if custom_prompt and custom_prompt.strip():
        user_prompt = user_prompt + "\n\n【用户附加要求】\n" + custom_prompt.strip()

    # 4. 读取产品白底图
    try:
        image = Image.open(image_path).convert("RGB")
    except Exception:
        return None, f"错误: 无法读取图片文件 {image_path}"

    # 5. 调用 Gemini API
    try:
        import httpx
        from google import genai
        from google.genai.types import GenerateContentConfig, HttpOptions, ImageConfig

        # 配置代理（国内访问 Google API 需要）
        http_options = None
        if settings.gemini_proxy:
            proxy_url = settings.gemini_proxy
            sync_client = httpx.Client(proxy=proxy_url)
            async_client = httpx.AsyncClient(proxy=proxy_url)
            http_options = HttpOptions(
                httpx_client=sync_client,
                httpx_async_client=async_client,
            )

        client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=http_options,
        )
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=[user_prompt, image],
            config=GenerateContentConfig(
                system_instruction=persona,
                response_modalities=["TEXT", "IMAGE"],
                image_config=ImageConfig(aspect_ratio="3:4"),
            ),
        )
    except Exception as e:
        return None, f"Gemini API 调用失败: {e}"

    # 6. 提取 TEXT（设计说明）和 IMAGE（生成的卡片图）
    design_description = ""
    image_bytes = None

    try:
        for part in response.candidates[0].content.parts:
            if part.text:
                design_description = part.text
            elif part.inlineData and part.inlineData.data:
                image_bytes = part.inlineData.data
    except (IndexError, AttributeError) as e:
        return None, f"无法解析 Gemini 响应: {e}"

    if image_bytes is None:
        return None, f"Gemini 未返回图片。设计说明: {design_description[:200]}"

    return image_bytes, design_description


async def _real_openai_generate(
    image_path: str,
    product_context: dict[str, str] | None = None,
    *,
    mode: str = "card_design",
    custom_prompt: str = "",
) -> tuple[bytes | None, str]:
    """通过 pantaqu 中转站调用 gpt-image-2 生成产品卡片图。

    使用 pantaqu 自定义 /g1/gptImage 端点，支持传入产品图（Base64）+ 俄语文案。
    策略：同步 json 模式 → 异步 polling 模式。

    Args:
        image_path: 下载到本地的产品白底图路径。
        product_context: 产品数据信息库（含俄语标题/副标题/功能点等）。
        mode: "card_design"（卡片设计）或 "translate"（图片翻译）。

    Returns:
        (image_bytes, design_description) — 生成的图片字节 + 设计说明。
        若生成失败，image_bytes 为 None。
    """
    import asyncio
    import base64
    import mimetypes
    import httpx

    from config import settings

    if not settings.image_gen_api_key:
        return None, "错误: 未配置 IMAGE_GEN_API_KEY"
    if not settings.image_gen_base_url:
        return None, "错误: 未配置 IMAGE_GEN_BASE_URL（中转站接口地址）"

    product_context = product_context or {}

    # 构建 prompt：translate 模式使用简单翻译 prompt，card_design 模式使用完整人设
    if mode == "translate":
        full_prompt = (
            "将图中的英文文字翻译为地道、自然的俄语，"
            "用于俄罗斯电商平台（Wildberries/Ozon）的商品展示。\n"
            "翻译要求：\n"
            "- 使用俄罗斯买家熟悉的电商用语，不要生硬直译\n"
            "- 保留品牌名和型号编号不翻译\n"
            "- 计量单位转换为俄罗斯通用的公制单位\n"
            "- 文字排版与原文风格保持一致，清晰可读\n"
            "- 所有文字必须使用正确的西里尔字母拼写"
        )
    else:
        persona = _load_gemini_card_persona()
        title = product_context.get("俄语标题", "")
        subtitle = product_context.get("核心流量词", "")
        features = _extract_features(product_context)
        category = product_context.get("product_category", "")
        user_prompt = _build_design_prompt(title, subtitle, features, category)

        prompt_parts = []
        if persona:
            prompt_parts.append(persona.strip()[:1500])
        prompt_parts.append("=== 设计任务 ===")
        prompt_parts.append(user_prompt)
        prompt_parts.append("请直接生成最终的 Wildberries/Ozon 风格产品卡片图。")
        prompt_parts.append("所有文字必须使用俄语，排版清晰专业，3:4 比例。")
        full_prompt = "\n".join(prompt_parts)

    # 追加用户自定义提示词
    if custom_prompt and custom_prompt.strip():
        full_prompt = full_prompt + "\n\n【用户附加要求】\n" + custom_prompt.strip()

    # 3. 读取产品白底图并编码为 Base64 data URL
    image_b64_url: str = ""
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
        mime_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"
        b64 = base64.b64encode(image_data).decode("utf-8")
        image_b64_url = f"data:{mime_type};base64,{b64}"
    except Exception:
        # 无图时继续（纯文生图）
        pass

    # 4. 通用请求配置
    model = settings.image_gen_model or "gpt-image-2"
    base_url = settings.image_gen_base_url.rstrip("/")
    headers_auth = {
        "Authorization": f"Bearer {settings.image_gen_api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "prompt": full_prompt,
        "images": [image_b64_url] if image_b64_url else [],
        "aspectRatio": "3:4",
    }

    async def _poll_task(task_id: str) -> tuple[bytes | None, str]:
        """轮询 /poll/result 直到任务完成（最多等待 5 分钟）。

        复用已有任务 ID，不创建新任务。
        """
        poll_url = f"{base_url}/poll/result"
        max_attempts = 60
        for _ in range(max_attempts):
            await asyncio.sleep(5)
            try:
                async with httpx.AsyncClient(timeout=15.0) as session:
                    resp = await session.post(
                        poll_url,
                        headers=headers_auth,
                        json={"id": task_id},
                    )
                    if resp.status_code != 200:
                        continue
                    poll_result = resp.json()
            except httpx.HTTPError:
                continue

            status = poll_result.get("status", "")
            if status == "succeeded":
                return _parse_pantaqu_response(poll_result)
            elif status in ("failed", "violation"):
                error = poll_result.get("error", status)
                return None, f"gptImage 生成失败: {error} (task: {task_id})"

        return None, f"gptImage 轮询超时（{max_attempts * 5}s），task: {task_id}"

    async def _submit_and_poll(reply_type: str) -> tuple[bytes | None, str]:
        """POST /g1/gptImage 提交任务，如需轮询则复用任务 ID。

        策略：
        1. 以 replyType 提交，得到响应
        2. 若同步完成 → 直接返回结果（1 次请求）
        3. 若 status="running" → 复用响应中的任务 ID 去轮询（不创建新任务）
        4. 若失败/违规 → 返回错误
        """
        api_url = f"{base_url}/g1/gptImage"
        submit_payload = {**payload, "replyType": reply_type}

        try:
            async with httpx.AsyncClient(
                timeout=180.0 if reply_type == "json" else 30.0
            ) as session:
                resp = await session.post(api_url, headers=headers_auth, json=submit_payload)
                if resp.status_code != 200:
                    return None, f"gptImage API 返回 {resp.status_code}: {resp.text[:500]}"
                result = resp.json()
        except httpx.HTTPError as e:
            return None, f"gptImage API 网络错误: {e}"

        status = result.get("status", "")

        # 同步完成 → 直接返回
        if status == "succeeded" or (
            reply_type == "json" and status not in ("running", "failed", "violation")
        ):
            return _parse_pantaqu_response(result)

        # 失败/违规 → 返回错误
        if status in ("failed", "violation"):
            return None, f"gptImage 生成失败: {result.get('error', status)}"

        # status == "running" → 复用任务 ID 轮询，不创建新任务
        task_id = result.get("id", "")
        if not task_id:
            return None, f"gptImage 返回 running 但无任务 ID: {str(result)[:500]}"

        return await _poll_task(task_id)

    # 先尝试同步模式（replyType=json），失败则复用同一任务 ID 轮询
    img_bytes, error_msg = await _submit_and_poll("json")
    if img_bytes is not None:
        return img_bytes, error_msg

    # 同步路径完全失败（网络错误/无任务ID），降级尝试异步提交
    img_bytes, error_msg2 = await _submit_and_poll("async")
    if img_bytes is not None:
        return img_bytes, error_msg2

    return None, f"生图失败。Sync: {error_msg} | Async: {error_msg2}"


def _parse_pantaqu_response(result: dict) -> tuple[bytes | None, str]:
    """解析 pantaqu /g1/gptImage 或 /poll/result 的响应。

    支持两种格式：
    1. results[].url → HTTP 下载图片
    2. results[].b64_json → Base64 解码图片

    Returns:
        (image_bytes, design_description)
    """
    import base64
    import httpx

    image_bytes = None
    design_description = ""

    try:
        results = result.get("results", [])
        if not results:
            # 可能在顶层有 url
            url = result.get("url", "")
            if url:
                results = [{"url": url}]

        for r in results:
            # URL 格式
            url = r.get("url", "")
            if url and image_bytes is None:
                try:
                    with httpx.Client(timeout=60.0) as client:
                        resp = client.get(url)
                        if resp.status_code == 200:
                            image_bytes = resp.content
                except Exception:
                    pass

            # Base64 格式
            b64_data = r.get("b64_json", "")
            if b64_data and image_bytes is None:
                try:
                    image_bytes = base64.b64decode(b64_data)
                except Exception:
                    pass

        design_description = result.get("prompt", result.get("revised_prompt", ""))
        if not design_description:
            design_description = "由 gpt-image-2 生成（pantaqu）"

    except (KeyError, IndexError, TypeError, AttributeError):
        pass

    return image_bytes, design_description


# ═══════════════════════════════════════════════════════════
# 统一生成调度 — 根据 image_gen_provider 选择后端
# ═══════════════════════════════════════════════════════════


async def _dispatch_generate(
    image_path: str,
    product_context: dict[str, str] | None = None,
    *,
    mode: str = "card_design",
    custom_prompt: str = "",
) -> tuple[bytes | None, str]:
    """根据 settings.image_gen_provider 自动选择生成后端。

    - "gemini" → _real_gemini_generate
    - "openai_compatible" → _real_openai_generate
    """
    from config import settings

    provider = settings.image_gen_provider
    if provider == "openai_compatible":
        return await _real_openai_generate(image_path, product_context, mode=mode, custom_prompt=custom_prompt)
    else:
        # 默认走 Gemini
        return await _real_gemini_generate(image_path, product_context, mode=mode, custom_prompt=custom_prompt)


def _save_generated_image(image_bytes: bytes, asin: str, index: int) -> str:
    """保存生成的图片到本地 images/asin/XX_card.jpg。

    Args:
        image_bytes: Gemini 返回的图片字节。
        asin: 产品 ASIN。
        index: 图片序号（0-based）。

    Returns:
        本地文件路径。
    """
    out_dir = os.path.join("images", asin)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{index:02d}_card.jpg")

    from io import BytesIO

    img = Image.open(BytesIO(image_bytes))
    img.save(out_path, "JPEG", quality=95)
    return out_path


# ═══════════════════════════════════════════════════════════
# 重试包装器（从 image_translator 复用）
# ═══════════════════════════════════════════════════════════

from image_translator import _retry_step, _call_async  # noqa: E402


# ═══════════════════════════════════════════════════════════
# Dataclass 结果类型
# ═══════════════════════════════════════════════════════════


@dataclass
class CardResult:
    """单张产品卡片的生成结果。

    Attributes:
        index: 图片在 ASIN 内的序号（0-based）。
        original_url: 原始产品图片 URL。
        r2_url: 生成后的 R2 公开 URL（失败时回退为原始 URL）。
        local_path: 本地存档路径。
        status: "ok" | "error" | "skipped" | "video"。
        error: 错误信息（status=error 时非空）。
        retry_count: 该卡片总重试次数。
        design_description: Gemini 返回的设计说明（TEXT part）。
    """
    index: int = 0
    original_url: str = ""
    r2_url: str = ""
    local_path: str = ""
    status: str = "ok"
    error: str = ""
    retry_count: int = 0
    design_description: str = ""


@dataclass
class AsinCardResult:
    """单个 ASIN 的所有卡片生成结果。

    Attributes:
        asin: 产品 ASIN。
        cards: 该 ASIN 下所有卡片的生成结果。
        success_count: status="ok" 的卡片数。
        error_count: status="error" 的卡片数。
        skipped_count: status="skipped" 的条目数。
        video_count: status="video" 的条目数。
    """
    asin: str = ""
    cards: list[CardResult] = field(default_factory=list)
    success_count: int = 0
    error_count: int = 0
    skipped_count: int = 0
    video_count: int = 0


@dataclass
class BatchCardResult:
    """批量卡片生成结果。

    Attributes:
        results: 每个 ASIN 的处理结果。
        total_asins: 总 ASIN 数。
        completed_asins: 已完成的 ASIN 数。
        total_cards: 总卡片数。
        success_cards: 成功卡片数。
        error_cards: 失败卡片数。
        skipped_cards: 跳过卡片数。
        video_cards: 视频链接数。
        started_at: 开始时间 ISO 字符串。
        finished_at: 结束时间 ISO 字符串。
    """
    results: list[AsinCardResult] = field(default_factory=list)
    total_asins: int = 0
    completed_asins: int = 0
    total_cards: int = 0
    success_cards: int = 0
    error_cards: int = 0
    skipped_cards: int = 0
    video_cards: int = 0
    started_at: str = ""
    finished_at: str = ""


# ═══════════════════════════════════════════════════════════
# 管线函数
# ═══════════════════════════════════════════════════════════


async def generate_product_card(
    image_url: str,
    asin: str,
    index: int,
    *,
    product_context: dict[str, str] | None = None,
    mode: str = "card_design",
    custom_prompt: str = "",
    _download_func: Callable | None = None,
    _generate_func: Callable | None = None,
    _resize_func: Callable | None = None,
    _upload_func: Callable | None = None,
) -> CardResult:
    """执行单张产品图片的完整卡片生成管线。

    管线步骤：下载 → Gemini 生成 → 缩放(900×1200) → 上传+本地存档。

    每个步骤失败时，按容错降级链处理：跳过受影响步骤，继续后续步骤。

    Args:
        image_url: 原始产品图片 URL（白底图）。
        asin: 产品 ASIN。
        index: 图片序号（0-based）。
        product_context: 产品数据信息库（含俄语标题/副标题/功能点等）。
            Gemini 会结合此上下文做场景化设计。
        _download_func: 下载函数（测试注入）。
        _generate_func: Gemini 生成函数（测试注入）。
        _resize_func: 缩放函数（测试注入）。
        _upload_func: 上传函数（测试注入）。

    Returns:
        CardResult 包含处理状态和结果。
    """
    result = CardResult(
        index=index,
        original_url=image_url,
        status="ok",
    )

    # 解析真实实现（测试可注入 mock）
    _download = _download_func or _real_download
    _generate = _generate_func or _dispatch_generate
    _upload = _upload_func or _real_upload

    # ── 步骤1: 下载 ──
    local_path = ""
    try:
        local_path = await _download(image_url) if asyncio.iscoroutinefunction(_download) else _download(image_url)
    except Exception as e:
        result.status = "error"
        result.error = f"下载失败: {e}"
        result.r2_url = image_url
        return result

    # ── 步骤2: translate 模式下 OCR 检测，无文字则跳过 AI ──
    image_bytes: bytes | None = None
    design_description = ""

    if mode == "translate":
        has_text = await _image_has_text(local_path)
        if not has_text:
            design_description = "无文字，跳过翻译"
            # 跳过 AI 生成，直接用原图走缩放+上传
            result.design_description = design_description
            try:
                img = Image.open(local_path) if os.path.isfile(local_path) else Image.new("RGB", (900, 1200))
                if _resize_func:
                    resized = _resize_func(img)
                    resized = await resized if asyncio.iscoroutine(resized) else resized
                else:
                    resized = resize_to_3x4(img)
                out_path = _save_image(resized, asin, index)
                result.local_path = out_path
                remote_key = f"{asin}/{index:02d}_card.jpg"
                r2 = _upload(out_path, remote_key)
                result.r2_url = await r2 if asyncio.iscoroutine(r2) else r2
            except Exception as e:
                result.status = "error"
                result.error = f"无文字图片处理失败: {e}"
                result.r2_url = image_url
            return result

    # ── 步骤3: AI 生成（含自动重试 1 次）──
    async def _do_generate():
        if asyncio.iscoroutinefunction(_generate):
            raw = _generate(local_path, product_context, mode=mode, custom_prompt=custom_prompt)
            return await raw
        elif asyncio.iscoroutine(_generate):
            return await _generate
        else:
            raw = _call_async(_generate, local_path, product_context, mode=mode, custom_prompt=custom_prompt)
            return await raw

    try:
        image_bytes, design_description = await _retry_step(
            "AI 生成",
            _do_generate(),
            max_retries=2,
            backoff_base=3,
            timeout=360.0,  # 6 分钟，涵盖中转站异步轮询（最长 5 分钟）
        )
    except Exception as e:
        result.status = "error"
        result.error = f"Gemini 生成失败（含自动重试）: {e}"
        result.design_description = str(e)
        # 失败时仍尝试 resize + upload 原图
        try:
            img = Image.open(local_path) if os.path.isfile(local_path) else Image.new("RGB", (900, 1200))
            if _resize_func:
                resized = _resize_func(img)
                resized = await resized if asyncio.iscoroutine(resized) else resized
            else:
                resized = resize_to_3x4(img)
            out_path = _save_image(resized, asin, index)
            result.local_path = out_path
            remote_key = f"{asin}/{index:02d}_card.jpg"
            r2 = _upload(out_path, remote_key)
            result.r2_url = await r2 if asyncio.iscoroutine(r2) else r2
        except Exception as e2:
            result.error += f" + 后续处理失败: {e2}"
            result.r2_url = image_url
        return result

    result.design_description = design_description

    if image_bytes is None:
        result.status = "error"
        result.error = f"Gemini 未生成图片: {design_description[:200]}"
        result.r2_url = image_url
        return result

    # ── translate 模式：核查输出图是否仍有未翻译的英文 ──
    if mode == "translate":
        passed, english_texts = await _verify_translation_output(image_bytes)
        if not passed:
            # 核查失败：输出图中仍有显著英文残留
            sample = english_texts[:5]
            result.status = "error"
            result.error = (
                f"翻译核查失败：输出图仍有 {len(english_texts)} 处英文残留，"
                f"示例: {sample}"
            )
            result.design_description = design_description
            result.r2_url = image_url
            return result
        # 核查通过 → 继续执行步骤4（缩放+上传）

    # ── 步骤4: 缩放 → 本地存档 ──
    try:
        from io import BytesIO
        img = Image.open(BytesIO(image_bytes))

        if _resize_func:
            resized = _resize_func(img)
            resized = await resized if asyncio.iscoroutine(resized) else resized
        else:
            resized = resize_to_3x4(img)

        out_path = _save_image(resized, asin, index)
        result.local_path = out_path
    except Exception as e:
        result.status = "error"
        result.error = f"图片缩放/保存失败: {e}"
        result.r2_url = image_url
        return result

    # ── 步骤5: 上传 R2 ──
    try:
        remote_key = f"{asin}/{index:02d}_card.jpg"
        r2 = _upload(out_path, remote_key)
        result.r2_url = await r2 if asyncio.iscoroutine(r2) else r2
    except Exception as e:
        result.status = "error"
        result.error = f"R2 上传失败: {e}"
        result.r2_url = image_url

    return result


def _save_image(image: Image.Image, asin: str, index: int) -> str:
    """保存 PIL Image 到本地 images/asin/XX_card.jpg。"""
    out_dir = os.path.join("images", asin)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{index:02d}_card.jpg")
    image.save(out_path, "JPEG", quality=95)
    return out_path


async def generate_card_from_local_image(
    local_path: str,
    asin: str,
    index: int,
    *,
    product_context: dict[str, str] | None = None,
    mode: str = "card_design",
    custom_prompt: str = "",
    _generate_func: Callable | None = None,
    _resize_func: Callable | None = None,
    _upload_func: Callable | None = None,
) -> CardResult:
    """从本地图片文件直接生成产品卡片（跳过下载步骤）。

    用于用户直接上传图片的场景，无需提供图片 URL。

    Args:
        local_path: 本地图片文件路径。
        asin: 产品 ASIN 或标识符。
        index: 图片序号（0-based）。
        product_context: 产品数据信息库（可选，无 Excel 时可为空字典）。
        mode: "card_design"（卡片设计）或 "translate"（图片翻译）。
        custom_prompt: 用户自定义提示词。
        _generate_func: AI 生成函数（测试注入）。
        _resize_func: 缩放函数（测试注入）。
        _upload_func: 上传函数（测试注入）。

    Returns:
        CardResult 包含处理状态和结果。
    """
    result = CardResult(
        index=index,
        original_url=f"file://{local_path}",
        status="ok",
    )

    _generate = _generate_func or _dispatch_generate
    _upload = _upload_func or _real_upload

    # ── 步骤1: translate 模式下 OCR 检测，无文字则跳过 AI ──
    image_bytes: bytes | None = None
    design_description = ""

    if mode == "translate":
        has_text = await _image_has_text(local_path)
        if not has_text:
            design_description = "无文字，跳过翻译"
            result.design_description = design_description
            try:
                img = Image.open(local_path) if os.path.isfile(local_path) else Image.new("RGB", (900, 1200))
                if _resize_func:
                    resized = _resize_func(img)
                    resized = await resized if asyncio.iscoroutine(resized) else resized
                else:
                    resized = resize_to_3x4(img)
                out_path = _save_image(resized, asin, index)
                result.local_path = out_path
                remote_key = f"{asin}/{index:02d}_card.jpg"
                r2 = _upload(out_path, remote_key)
                result.r2_url = await r2 if asyncio.iscoroutine(r2) else r2
            except Exception as e:
                result.status = "error"
                result.error = f"无文字图片处理失败: {e}"
                result.r2_url = ""
            return result

    # ── 步骤2: AI 生成（含自动重试）──
    async def _do_generate():
        if asyncio.iscoroutinefunction(_generate):
            raw = _generate(local_path, product_context, mode=mode, custom_prompt=custom_prompt)
            return await raw
        elif asyncio.iscoroutine(_generate):
            return await _generate
        else:
            raw = _call_async(_generate, local_path, product_context, mode=mode, custom_prompt=custom_prompt)
            return await raw

    try:
        image_bytes, design_description = await _retry_step(
            "AI 生成",
            _do_generate(),
            max_retries=2,
            backoff_base=3,
            timeout=360.0,
        )
    except Exception as e:
        result.status = "error"
        result.error = f"生成失败（含自动重试）: {e}"
        result.design_description = str(e)
        # 失败时仍尝试 resize + upload 原图
        try:
            img = Image.open(local_path) if os.path.isfile(local_path) else Image.new("RGB", (900, 1200))
            if _resize_func:
                resized = _resize_func(img)
                resized = await resized if asyncio.iscoroutine(resized) else resized
            else:
                resized = resize_to_3x4(img)
            out_path = _save_image(resized, asin, index)
            result.local_path = out_path
            remote_key = f"{asin}/{index:02d}_card.jpg"
            r2 = _upload(out_path, remote_key)
            result.r2_url = await r2 if asyncio.iscoroutine(r2) else r2
        except Exception as e2:
            result.error += f" + 后续处理失败: {e2}"
        return result

    result.design_description = design_description

    if image_bytes is None:
        result.status = "error"
        result.error = f"未生成图片: {design_description[:200]}"
        return result

    # ── translate 模式：核查输出图是否仍有未翻译的英文 ──
    if mode == "translate":
        passed, english_texts = await _verify_translation_output(image_bytes)
        if not passed:
            sample = english_texts[:5]
            result.status = "error"
            result.error = (
                f"翻译核查失败：输出图仍有 {len(english_texts)} 处英文残留，"
                f"示例: {sample}"
            )
            result.design_description = design_description
            return result

    # ── 步骤3: 缩放 → 本地存档 ──
    try:
        from io import BytesIO
        img = Image.open(BytesIO(image_bytes))

        if _resize_func:
            resized = _resize_func(img)
            resized = await resized if asyncio.iscoroutine(resized) else resized
        else:
            resized = resize_to_3x4(img)

        out_path = _save_image(resized, asin, index)
        result.local_path = out_path
    except Exception as e:
        result.status = "error"
        result.error = f"图片缩放/保存失败: {e}"
        return result

    # ── 步骤4: 上传 R2 ──
    try:
        remote_key = f"{asin}/{index:02d}_card.jpg"
        r2 = _upload(out_path, remote_key)
        result.r2_url = await r2 if asyncio.iscoroutine(r2) else r2
    except Exception as e:
        result.status = "error"
        result.error = f"R2 上传失败: {e}"

    return result


async def generate_asin_cards(
    product: dict,
    *,
    mode: str = "card_design",
    custom_prompt: str = "",
    _download_func: Callable | None = None,
    _generate_func: Callable | None = None,
    _resize_func: Callable | None = None,
    _upload_func: Callable | None = None,
) -> AsinCardResult:
    """并发处理单个 ASIN 的所有图片卡片生成。

    Args:
        product: 产品字典，需包含 asin 和 图片url（以 " | " 或 ";" 分隔的多 URL）。
            可选 product_context 字段（产品数据信息库）。
        _*_func: 测试注入用。

    Returns:
        AsinCardResult。
    """
    asin = product.get("asin", "")
    image_urls_str = product.get("图片url", "")
    product_context = product.get("product_context", None)

    import re as _re
    all_urls = [u.strip() for u in _re.split(r'[;|]', image_urls_str) if u.strip()]

    if not all_urls:
        return AsinCardResult(asin=asin)

    # 分离视频 URL 和图片 URL
    try:
        from phase1_extractor import _is_video_url
    except ImportError:
        def _is_video_url(url: str) -> bool:
            return any(k in url.lower() for k in [".m3u8", "vse-vms-transcoding", ".mp4"])

    video_urls: list[tuple[int, str]] = []
    image_urls: list[tuple[int, str]] = []

    for i, url in enumerate(all_urls):
        if _is_video_url(url):
            video_urls.append((i, url))
        else:
            image_urls.append((i, url))

    # 并发处理图片 URL
    tasks = []
    for orig_idx, url in image_urls:
        tasks.append(
            generate_product_card(
                image_url=url,
                asin=asin,
                index=orig_idx,
                product_context=product_context,
                mode=mode,
                custom_prompt=custom_prompt,
                _download_func=_download_func,
                _generate_func=_generate_func,
                _resize_func=_resize_func,
                _upload_func=_upload_func,
            )
        )

    card_results = await asyncio.gather(*tasks) if tasks else []

    # 为视频 URL 创建占位结果
    video_results: list[CardResult] = []
    for orig_idx, video_url in video_urls:
        video_results.append(CardResult(
            index=orig_idx,
            original_url=video_url,
            r2_url=video_url,
            status="video",
        ))

    # 按原始序号合并并排序
    all_results = list(card_results) + video_results
    all_results.sort(key=lambda r: r.index)

    success = sum(1 for r in all_results if r.status == "ok")
    errors = sum(1 for r in all_results if r.status == "error")
    skipped = sum(1 for r in all_results if r.status == "skipped")
    videos = sum(1 for r in all_results if r.status == "video")

    return AsinCardResult(
        asin=asin,
        cards=list(all_results),
        success_count=success,
        error_count=errors,
        skipped_count=skipped,
        video_count=videos,
    )


async def generate_batch_cards(
    products: list[dict],
    progress_callback: Callable | None = None,
    resume_from: str | None = None,
    *,
    mode: str = "card_design",
    custom_prompt: str = "",
    _download_func: Callable | None = None,
    _generate_func: Callable | None = None,
    _resize_func: Callable | None = None,
    _upload_func: Callable | None = None,
) -> BatchCardResult:
    """批量处理产品卡片生成。

    支持断点续跑：若 resume_from（card_progress.json 路径）已有完成的 ASIN，则跳过。

    Args:
        products: 产品列表。
        progress_callback: 每个 ASIN 完成后的回调。
        resume_from: card_progress.json 路径。
        _*_func: 测试注入用。

    Returns:
        BatchCardResult。
    """
    import datetime
    import json as _json

    started_at = datetime.datetime.now().isoformat()

    # 断点续跑：读取已完成的 ASIN
    completed_asins: set[str] = set()
    if resume_from and os.path.isfile(resume_from):
        try:
            with open(resume_from, "r", encoding="utf-8") as f:
                progress_data = _json.load(f)
            completed_asins = set(progress_data.get("completed_asins", []))
        except (_json.JSONDecodeError, KeyError):
            pass

    results: list[AsinCardResult] = []
    total_cards = 0
    success_cards = 0
    error_cards = 0
    skipped_cards = 0
    video_cards = 0

    for product in products:
        asin = product.get("asin", "")

        # 跳过已完成的 ASIN
        if asin in completed_asins:
            continue

        asin_result = await generate_asin_cards(
            product=product,
            mode=mode,
            custom_prompt=custom_prompt,
            _download_func=_download_func,
            _generate_func=_generate_func,
            _resize_func=_resize_func,
            _upload_func=_upload_func,
        )
        results.append(asin_result)
        total_cards += len(asin_result.cards)
        success_cards += asin_result.success_count
        error_cards += asin_result.error_count
        skipped_cards += asin_result.skipped_count
        video_cards += asin_result.video_count

        # 写入 card_progress.json
        if resume_from:
            _write_card_progress(
                resume_from,
                completed_asins=list(completed_asins | {asin}),
                current_asin=asin,
                total_asins=len(products),
                total_cards=total_cards,
                processed_cards=success_cards + error_cards + skipped_cards,
                started_at=started_at,
            )
            completed_asins.add(asin)

        if progress_callback:
            if asyncio.iscoroutinefunction(progress_callback):
                await progress_callback(asin_result)
            else:
                progress_callback(asin_result)

    finished_at = datetime.datetime.now().isoformat()

    return BatchCardResult(
        results=results,
        total_asins=len(products),
        completed_asins=len(results),
        total_cards=total_cards,
        success_cards=success_cards,
        error_cards=error_cards,
        skipped_cards=skipped_cards,
        video_cards=video_cards,
        started_at=started_at,
        finished_at=finished_at,
    )


def _write_card_progress(
    filepath: str,
    completed_asins: list[str],
    current_asin: str,
    total_asins: int,
    total_cards: int,
    processed_cards: int,
    started_at: str,
) -> None:
    """原子写入 card_progress.json。"""
    import json as _json
    import os as _os
    from datetime import datetime as _dt

    data = {
        "state": "running",
        "completed_asins": completed_asins,
        "current_asin": current_asin,
        "total_asins": total_asins,
        "total_cards": total_cards,
        "processed_cards": processed_cards,
        "started_at": started_at,
        "updated_at": _dt.now().isoformat(),
    }

    tmp_path = filepath + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False)
    _os.replace(tmp_path, filepath)
