"""
Phase 1：AI 信息萃取模块。

将亚马逊产品页 HTML 交给 AI，提取完整的结构化产品属性（15 个字段），
写入 products.db。

公共接口：
    InformationExtractor  — 抽象基类
    OpenAIExtractor       — OpenAI 兼容协议实现
    ClaudeExtractor       — Anthropic Claude 实现
    MockExtractor         — Mock 实现（不依赖真实 API）
    create_extractor      — 工厂函数
    run_phase1            — 批量萃取入口
"""

import argparse
import json
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from text_utils import MARKDOWN_JSON_RE


# ============================================================
# System Prompt 加载
# ============================================================

_PHASE1_PERSONA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "prompts",
    "phase1_extraction_persona.txt",
)


def _load_phase1_prompt() -> str:
    """加载 Phase 1 萃取人设 System Prompt 文件。

    Returns:
        prompts/phase1_extraction_persona.txt 的完整内容。

    Raises:
        FileNotFoundError: 文件不存在时抛出。
    """
    path = Path(_PHASE1_PERSONA_PATH)
    if not path.is_file():
        raise FileNotFoundError(
            f"Phase 1 萃取人设文件不存在: {_PHASE1_PERSONA_PATH}\n"
            f"请确保 prompts/phase1_extraction_persona.txt 文件存在。"
        )
    return path.read_text(encoding="utf-8")


# ============================================================
# JSON 解析
# ============================================================

# Phase 1 必填字段（缺少任一字段时触发重试）
_PHASE1_REQUIRED_FIELDS = {"category", "features", "brand", "material"}

# Phase 1 全部字段及其默认值
_PHASE1_ALL_FIELDS: dict[str, object] = {
    "category": "",
    "material": "",
    "color": "",
    "dimensions": "",
    "weight": "",
    "capacity": "",
    "package_contents": "",
    "features": [],
    "technical_specs": {},
    "target_audience": "",
    "use_scenarios": [],
    "unique_selling_points": [],
    "brand": "",
    "en_search_keywords": [],
}

def _parse_phase1_response(text: str) -> dict:
    """从 AI 响应文本中解析 Phase 1 结构化 JSON。

    支持：
    1. 直接 JSON 解析
    2. 从 Markdown 代码块（```json ... ```）中提取

    必填字段（category/features/brand/material）缺失时抛出 ValueError。
    可选字段缺失时使用默认值（空字符串/空数组/空对象）。

    Args:
        text: AI 返回的原始响应文本。

    Returns:
        包含全部 15 个字段的字典。

    Raises:
        ValueError: 无法解析 JSON、或缺少必填字段时抛出。
    """
    if not text or not text.strip():
        raise ValueError("AI 返回空响应，无法解析 JSON")

    parsed: dict | None = None

    # 尝试 1：直接解析
    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # 尝试 2：从 Markdown 代码块提取
    if parsed is None:
        match = MARKDOWN_JSON_RE.search(text)
        if match:
            try:
                parsed = json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

    if parsed is None:
        raise ValueError(
            f"无法解析 AI 响应为 JSON。"
            f"响应前 200 字符: {text.strip()[:200]}"
        )

    if not isinstance(parsed, dict):
        raise ValueError(
            f"AI 返回的 JSON 不是对象类型: {type(parsed).__name__}"
        )

    # 补充默认值（可选字段缺失时使用默认值）
    result: dict = {}
    for field, default in _PHASE1_ALL_FIELDS.items():
        if field in parsed:
            value = parsed[field]
            # 对于数组/对象字段，验证类型
            if isinstance(default, list) and not isinstance(value, list):
                value = default
            elif isinstance(default, dict) and not isinstance(value, dict):
                value = default
            result[field] = value
        else:
            result[field] = default

    # 验证必填字段
    missing = {
        f for f in _PHASE1_REQUIRED_FIELDS
        if not result.get(f)
    }
    if missing:
        raise ValueError(
            f"AI 返回的 JSON 缺少必填字段或字段为空: "
            f"{', '.join(sorted(missing))}"
        )

    return result


# ============================================================
# 构建 User Message
# ============================================================

# HTML 预处理后的最大字符数（约 200K tokens，远低于 1M 上下文窗口）
_MAX_HTML_CHARS = 800_000


def _preprocess_html(html: str) -> str:
    """精简 HTML：移除脚本、样式等非内容元素，减少发送给 AI 的 token 数。

    亚马逊产品页原始 HTML 可达数 MB（>1M tokens），超出部分模型的上下文限制。
    此函数去除无意义标签后截断到 _MAX_HTML_CHARS。

    Args:
        html: 原始 HTML 源码。

    Returns:
        精简后的 HTML 文本。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")

    # 移除对语义提取无用的元素
    for tag in soup(["script", "style", "noscript", "link", "meta", "svg", "img",
                      "iframe", "input", "button", "select", "textarea"]):
        tag.decompose()

    # 移除 HTML 注释
    from bs4 import Comment
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    # 获取清理后的文本
    text = soup.get_text(separator="\n", strip=True)

    if len(text) > _MAX_HTML_CHARS:
        text = text[:_MAX_HTML_CHARS] + "\n\n[... HTML 过长，已截断 ...]"

    return text


def _build_user_message(html: str) -> str:
    """构建发送给 AI 的用户消息（包含产品页 HTML）。

    HTML 会先经 _preprocess_html 精简以控制在模型上下文限制内。

    Args:
        html: 亚马逊产品页 HTML 源码。

    Returns:
        用户消息字符串。
    """
    cleaned = _preprocess_html(html)
    return (
        f"请从以下亚马逊产品页 HTML 中提取完整的结构化产品属性。\n\n"
        f"=== 产品页 HTML（精简后）===\n{cleaned}\n\n"
        f"请严格按照 System Prompt 中定义的 JSON Schema 输出，不要输出任何其他内容。"
    )


# ============================================================
# 基础字段提取（替代旧 extractor.py 的确定性 HTML 解析）
# ============================================================

# 匹配亚马逊图片 URL 中的尺寸后缀，例如：
#   ._AC_US40_  ._SX38_SY50_  ._AC_SL1500_  ._SX425_  ._SS125_
_SIZE_SUFFIX_RE = re.compile(r"\._[A-Z0-9,_]+_")

# 替换低分辨率尺寸后缀为最大分辨率（1500px 长边）
_HIRES_SUFFIX = "._AC_SL1500_"


def _normalize_image_url(url: str, *, force_hires: bool = False) -> str:
    """移除亚马逊图片 URL 的尺寸限制后缀，可选强制最大分辨率。

    当 force_hires=True 时，将尺寸后缀替换为 _AC_SL1500_（1500px 长边），
    确保 alt images 也能获取高清版本（不再局限于 500px 默认值）。

    Args:
        url: 原始图片 URL。
        force_hires: 是否强制替换为高清尺寸后缀。

    Returns:
        处理后的图片 URL。
    """
    # 先去掉已有尺寸后缀
    base = _SIZE_SUFFIX_RE.sub("", url)
    if force_hires:
        # 在扩展名前插入高清尺寸后缀
        # URL 形如 .../images/I/XXXXX.jpg
        base = re.sub(r"(\.\w+)$", f"{_HIRES_SUFFIX}\\1", base)
    return base


def _is_video_thumbnail(img_tag) -> bool:
    """检测 <img> 标签是否为视频缩略图（而非产品图片）。

    检查 img 自身属性及所有祖先元素的 class/id 是否包含 video 相关关键词，
    以及 src URL 是否包含视频播放按钮等特征。

    Args:
        img_tag: BeautifulSoup Tag 对象。

    Returns:
        True 表示该标签是视频缩略图，应跳过。
    """
    # 检查 src URL 中的视频特征
    src = (img_tag.get("src") or "").lower()
    video_url_patterns = [
        "play-button", "pkplay", "video-thumb", "videoimg",
        "video", "_SX125_PKplay", "_SS125_PK",
    ]
    if any(p in src for p in video_url_patterns):
        return True

    # 检查祖先元素的 class/id
    for ancestor in img_tag.parents:
        cls = " ".join(ancestor.get("class", [])) if ancestor.get("class") else ""
        aid = (ancestor.get("id") or "").lower()
        combined = f"{cls.lower()} {aid}"
        if any(kw in combined for kw in ["video", "video-thumb", "videoimg"]):
            return True

    return False


def _extract_asin_from_html(html: str) -> str:
    """从 HTML 中提取产品 ASIN。

    优先级：
    1. JS 数据中的 'asin' 字段
    2. <link rel="canonical"> 中的 /dp/ASIN
    3. <input id="ASIN"> 的 value

    Args:
        html: 产品页 HTML 源码。

    Returns:
        ASIN 字符串，未找到时返回空字符串。
    """
    # 尝试 1: JS 数据中的 asin 字段
    m = re.search(r"""'asin'\s*:\s*'([A-Z0-9]{10})'""", html)
    if m:
        return m.group(1)

    # 尝试 2: canonical URL
    m = re.search(r'<link[^>]+rel="canonical"[^>]+href="[^"]*/dp/([A-Z0-9]{10})', html)
    if m:
        return m.group(1)

    # 尝试 3: input#ASIN
    m = re.search(r'id="ASIN"[^>]+value="([A-Z0-9]{10})"', html)
    if m:
        return m.group(1)

    return ""


def _page_has_videos(html: str) -> bool:
    """检测产品页是否包含视频。

    检查 JS 数据中的 videoCount、playVideoInImmersiveView 等标志。

    Args:
        html: 产品页 HTML 源码。

    Returns:
        True 表示页面包含产品视频。
    """
    # totalVideoCount > 0
    m = re.search(r"'totalVideoCount'\s*:\s*'(\d+)'", html)
    if m and int(m.group(1)) > 0:
        return True

    # playVideoInImmersiveView: true
    if re.search(r"'playVideoInImmersiveView'\s*:\s*true", html):
        return True

    # altImages 中是否有视频缩略图（PKplay 等特征）
    if re.search(r'PKplay|play-button-mb-image-grid', html):
        return True

    return False


def _extract_video_urls_from_js(html: str) -> list[str]:
    """从页面 JavaScript 数据中提取产品视频的真实流 URL。

    亚马逊在 image block 的 JS 数据中内嵌了 \"videos\" JSON 数组，
    每个视频包含 url（HLS .m3u8 流）、title、duration 等字段。
    此函数提取所有视频的 url，优先取 variant=MAIN 的（主视频）。

    这些 .m3u8 URL 可以通过 ffmpeg 等工具下载：
        ffmpeg -i \"<m3u8_url>\" -c copy output.mp4

    Args:
        html: 产品页 HTML 源码。

    Returns:
        视频流 URL 列表（已去重），可为空。
    """
    # 定位 \"videos\" JSON 数组
    # 查找模式: "videos":[  ...  ]  在 image block 数据中
    videos_start = html.find('"videos":[')
    if videos_start == -1:
        return []

    # 从 "videos":[ 之后开始解析，找到匹配的 ]
    bracket_start = html.find('[', videos_start)
    if bracket_start == -1:
        return []

    depth = 0
    in_string = False
    escape_next = False
    bracket_end = -1

    for i in range(bracket_start, len(html)):
        ch = html[i]
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                bracket_end = i
                break

    if bracket_end == -1:
        return []

    videos_json_str = html[bracket_start:bracket_end + 1]

    try:
        videos = json.loads(videos_json_str)
    except json.JSONDecodeError:
        return []

    if not isinstance(videos, list):
        return []

    seen: set[str] = set()
    urls: list[str] = []

    for video in videos:
        url = video.get("url", "")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    return urls


def _is_video_url(url: str) -> bool:
    """判断 URL 是否为视频流链接（而非图片）。

    通过 URL 模式识别视频，用于图片翻译管线中自动跳过视频 URL。
    识别特征：
    - Amazon VSE 视频转码服务（vse-vms-transcoding-artifact）
    - HLS 流（.m3u8）
    - 视频优化 MP4（productVideoOptimized.mp4）
    - 亚马逊产品页链接（回退情况下使用）

    Args:
        url: 待检查的 URL。

    Returns:
        True 表示该 URL 是视频链接。
    """
    video_patterns = [
        "vse-vms-transcoding-artifact",
        ".m3u8",
        "productVideoOptimized.mp4",
        "vse-vod",
        "videopreview.jobtemplate.mp4",
        "vse-vms-closed-captions",
    ]
    url_lower = url.lower()
    for pattern in video_patterns:
        if pattern in url_lower:
            return True
    return False


def _extract_hi_res_urls_from_js(html: str) -> list[str]:
    """从页面 JavaScript 数据中提取 hiRes 高清图片 URL。

    亚马逊在页面 <script> 中内嵌了 colorImages 等 JSON 数据，
    每张产品图都有 thumb（缩略图，~100px）、large、hiRes（高清，1500px）三种 URL。
    此函数提取所有 hiRes URL，去重并保持出现顺序。

    相比从 #altImages <img> 标签提取缩略图 src 再替换尺寸后缀，
    此方法直接获取真正的 1500px 高清图 URL（图片 ID 与缩略图完全不同）。

    Args:
        html: 产品页 HTML 源码。

    Returns:
        去重后的 hiRes URL 列表。
    """
    seen: set[str] = set()
    urls: list[str] = []

    # 匹配所有 "hiRes":"<url>" 模式
    for m in re.finditer(r'"hiRes"\s*:\s*"([^"]+)"', html):
        url = m.group(1)
        if url not in seen:
            seen.add(url)
            urls.append(url)

    return urls


def _extract_basic_fields(html: str) -> dict[str, str]:
    """从亚马逊产品页 HTML 中提取标题、图片 URL、详情。

    替代已移除的 extractor.py 的确定性 HTML 解析功能。
    Phase 1 AI 萃取负责 15 个语义字段，
    本函数负责 3 个原始字段（标题、图片url、详情）。

    图片 URL 优先从页面 JS 数据中的 hiRes 字段提取（真正的高清图），
    回退到从 #landingImage / #altImages 解析。

    Args:
        html: 产品页 HTML 源码。

    Returns:
        包含 title, image_urls, details 三个键的字典。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")

    # ── 标题 ──
    title = ""
    title_el = soup.select_one("#productTitle")
    if title_el:
        title = title_el.get_text(strip=True)

    # ── ASIN（用于构建产品页视频链接）──
    asin = _extract_asin_from_html(html) or "UNKNOWN"

    # ── 图片 URL ──
    # 优先：从 JS 数据中提取 hiRes 高清 URL
    hi_res_urls = _extract_hi_res_urls_from_js(html)
    if hi_res_urls:
        url_parts = list(hi_res_urls)
    else:
        # 回退：从 <img> 标签提取（旧逻辑）
        seen: set[str] = set()
        url_parts: list[str] = []

        # 主图
        for selector in ["#landingImage", "#imgTagWrapperId img"]:
            main_img = soup.select_one(selector)
            if main_img:
                src = main_img.get("data-old-hires", "") or main_img.get("src", "")
                if src:
                    normalized = _normalize_image_url(src)
                    if normalized not in seen:
                        seen.add(normalized)
                        url_parts.append(normalized)
                break

        # 备图（缩略图区）
        alt_container = soup.select_one("#altImages")
        if alt_container:
            for img in alt_container.find_all("img"):
                if _is_video_thumbnail(img):
                    continue  # 跳过视频缩略图（用产品页 URL 代替，见下方）
                src = img.get("data-old-hires", "") or img.get("src", "")
                if not src:
                    continue
                base = _normalize_image_url(src)
                if base in seen:
                    continue
                seen.add(base)
                if not img.get("data-old-hires"):
                    final_url = _normalize_image_url(src, force_hires=True)
                else:
                    final_url = base
                url_parts.append(final_url)

    # ── 视频 URL ──
    # 从 JS 数据中提取真实的视频流 URL（.m3u8/.mp4），可下载后用 ffmpeg 转码上传。
    # 不加任何前缀标记，图片翻译管线通过 URL 模式（vse-vms-transcoding / .m3u8 等）自动识别并跳过。
    video_urls = _extract_video_urls_from_js(html)
    for vu in video_urls:
        if vu not in url_parts:
            url_parts.append(vu)

    # 如果 JS 中没找到视频 URL 但页面有视频标志（PKplay 缩略图等），
    # 回退到产品页链接
    if not video_urls and _page_has_videos(html):
        fallback_url = f"https://www.amazon.com/dp/{asin}"
        if fallback_url not in url_parts:
            url_parts.append(fallback_url)

    image_urls = ";".join(url_parts)

    # ── 详情 ──
    parts: list[str] = []

    # Feature bullets
    bullets = soup.select_one("#feature-bullets")
    if bullets:
        for span in bullets.select("span.a-list-item"):
            text = span.get_text(strip=True)
            if text:
                parts.append(text)

    # Product description
    desc = soup.select_one("#productDescription")
    if desc:
        text = desc.get_text(strip=True)
        if text:
            parts.append(text)

    details = "\n".join(parts)

    return {"title": title, "image_urls": image_urls, "details": details}


# ============================================================
# 抽象接口
# ============================================================

class InformationExtractor(ABC):
    """AI 信息萃取抽象基类。

    子类只需实现 _call_api 方法。extract 方法提供统一的
    解析、重试逻辑。
    """

    @abstractmethod
    def _call_api(self, html: str) -> str:
        """调用具体的 AI API，返回原始响应文本。

        Args:
            html: 亚马逊产品页 HTML 源码。

        Returns:
            API 的原始响应文本。
        """
        ...

    def extract(self, html: str) -> dict:
        """调用 API 萃取产品信息，JSON 解析失败时自动重试一次。

        Args:
            html: 亚马逊产品页 HTML 源码。

        Returns:
            包含全部 15 个产品属性字段的字典。
        """
        raw = self._call_api(html)

        try:
            parsed = _parse_phase1_response(raw)
        except ValueError:
            # 重试一次
            raw = self._call_api(html)
            parsed = _parse_phase1_response(raw)

        return parsed


# ============================================================
# 具体实现
# ============================================================

class OpenAIExtractor(InformationExtractor):
    """OpenAI 兼容协议萃取服务。

    支持 OpenAI、DeepSeek 及其他兼容 API。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "",
        model: str = "gpt-4o",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    def _call_api(self, html: str) -> str:
        """调用 OpenAI 兼容 API，返回原始响应文本。"""
        from openai import OpenAI

        kwargs: dict = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        client = OpenAI(**kwargs)

        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _load_phase1_prompt()},
                {"role": "user", "content": _build_user_message(html)},
            ],
            temperature=0.1,
        )

        return response.choices[0].message.content or ""


class ClaudeExtractor(InformationExtractor):
    """Anthropic Claude 萃取服务。"""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self.api_key = api_key
        self.model = model

    def _call_api(self, html: str) -> str:
        """调用 Anthropic API，返回原始响应文本。"""
        from anthropic import Anthropic

        client = Anthropic(api_key=self.api_key)

        response = client.messages.create(
            model=self.model,
            max_tokens=4096,
            temperature=0.1,
            system=_load_phase1_prompt(),
            messages=[
                {"role": "user", "content": _build_user_message(html)},
            ],
        )

        # Claude 响应可能是 TextBlock 列表
        content = response.content
        if isinstance(content, list):
            return "".join(
                block.text for block in content if hasattr(block, "text")
            )
        return str(content)


class MockExtractor(InformationExtractor):
    """Mock 萃取器，返回固定测试数据，不依赖真实 API。

    用于单元测试和开发调试。
    """

    def _call_api(self, html: str) -> str:
        """Mock 实现不调用 API，直接返回预设 JSON。"""
        return json.dumps({
            "category": "Beauty & Personal Care",
            "material": "ABS Plastic, Silicone",
            "color": "White",
            "dimensions": "15 x 10 x 5 cm",
            "weight": "350g",
            "capacity": "",
            "package_contents": "1 x Device, 1 x USB Cable, 1 x Manual",
            "features": [
                "Facial massage",
                "Body contouring",
                "LED light therapy",
                "5 adjustable modes",
            ],
            "technical_specs": {"Power": "5W", "Battery": "2000mAh"},
            "target_audience": "Women 25-45",
            "use_scenarios": ["Home spa", "Daily skincare routine"],
            "unique_selling_points": [
                "5-in-1 multifunction",
                "Portable USB charging",
            ],
            "brand": "BeautyPro",
            "en_search_keywords": [
                "face sculpting machine",
                "facial massager",
                "body contouring device",
                "LED therapy",
            ],
        }, ensure_ascii=False)


# ============================================================
# 工厂函数
# ============================================================

# 使用 OpenAI 兼容协议的 provider 名称集合
_OPENAI_COMPATIBLE_PROVIDERS = {"openai", "deepseek", "custom"}

# 所有支持的 provider 名称
_SUPPORTED_PROVIDERS = _OPENAI_COMPATIBLE_PROVIDERS | {"anthropic", "mock"}


def create_extractor(
    provider_name: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> InformationExtractor:
    """根据配置创建萃取服务实例。

    参数未提供时从 config.settings 读取。

    Args:
        provider_name: 服务商名称。None 时从配置读取。
        api_key: API 密钥。None 时从配置读取。
        base_url: 自定义 API 地址。None 时从配置读取。
        model: 模型名称。None 时从配置读取。

    Returns:
        InformationExtractor 实例。

    Raises:
        ValueError: provider_name 不支持或 api_key 为空时抛出。
    """
    from config import settings

    name = (provider_name or settings.phase1_api_provider).lower().strip()
    key = (api_key or settings.phase1_api_key).strip()
    url = (base_url or settings.phase1_api_base_url).strip()
    mdl = (model or settings.phase1_model).strip()

    # Mock 不需要 API Key
    if name == "mock":
        return MockExtractor()

    if not key:
        raise ValueError(
            "Phase 1 API Key 未配置。请在 .env 文件中设置 PHASE1_API_KEY，"
            "或设置环境变量 PHASE1_API_KEY。"
        )

    if name not in _SUPPORTED_PROVIDERS:
        raise ValueError(
            f"不支持的 Phase 1 服务商: '{name}'。"
            f"支持的选项: {', '.join(sorted(_SUPPORTED_PROVIDERS))}"
        )

    if name in _OPENAI_COMPATIBLE_PROVIDERS:
        return OpenAIExtractor(
            api_key=key,
            base_url=url,
            model=mdl,
        )

    if name == "anthropic":
        return ClaudeExtractor(
            api_key=key,
            model=mdl,
        )

    raise ValueError(f"不支持的 Phase 1 服务商: '{name}'")


# ============================================================
# 批量萃取
# ============================================================

def run_phase1(
    asins: list[str],
    html_dir: str,
    db: "sqlite3.Connection",
    extractor: InformationExtractor | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """批量萃取产品信息并写入数据库。

    遍历 ASIN → 读取 html/{ASIN}.html → 调 AI 萃取 → 写入 products 表。

    Args:
        asins: ASIN 列表。
        html_dir: HTML 存档目录路径。
        db: sqlite3 连接对象（需已调用 init_db）。
        extractor: InformationExtractor 实例。None 时通过 create_extractor() 创建。
        progress_callback: 可选回调，签名 (current: int, total: int)。

    Returns:
        结果列表，每项含 asin 和可选的 error 字段。
    """
    import sqlite3

    from db import upsert_product

    if not asins:
        return []

    # 创建 extractor（如果未提供）
    if extractor is None:
        try:
            extractor = create_extractor()
        except ValueError as e:
            # API Key 未配置 → 全部返回 error，不崩溃
            return [{"asin": asin, "error": str(e)} for asin in asins]

    total = len(asins)
    results: list[dict] = []

    for i, asin in enumerate(asins, start=1):
        html_path = os.path.join(html_dir, f"{asin}.html")

        # 读取 HTML 文件
        if not os.path.isfile(html_path):
            error_msg = f"HTML 文件不存在: {html_path}"
            results.append({"asin": asin, "error": error_msg})
            if progress_callback:
                progress_callback(i, total)
            continue

        try:
            html_content = Path(html_path).read_text(encoding="utf-8")
        except Exception as e:
            error_msg = f"读取 HTML 文件失败: {e}"
            results.append({"asin": asin, "error": error_msg})
            if progress_callback:
                progress_callback(i, total)
            continue

        # AI 萃取
        try:
            product_data = extractor.extract(html_content)
        except Exception as e:
            error_msg = f"AI 萃取失败: {e}"
            results.append({"asin": asin, "error": error_msg})
            if progress_callback:
                progress_callback(i, total)
            continue

        # 写入数据库
        try:
            # 从 HTML 中提取基础字段（标题、图片 URL、详情）
            basic_fields = _extract_basic_fields(html_content)

            product_record = {
                "asin": asin,
                "html_path": f"html/{asin}.html",
                "title": basic_fields["title"],
                "details": basic_fields["details"],
                "image_urls": basic_fields["image_urls"],
                **product_data,
            }
            upsert_product(db, product_record)
            results.append({"asin": asin})
        except Exception as e:
            error_msg = f"写入数据库失败: {e}"
            results.append({"asin": asin, "error": error_msg})

        # 进度回调
        if progress_callback:
            progress_callback(i, total)

    return results


# ============================================================
# CLI 入口
# ============================================================


def _parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。

    Args:
        argv: 命令行参数列表，None 时使用 sys.argv。

    Returns:
        包含 html_dir 和 db 属性的 Namespace。
    """
    parser = argparse.ArgumentParser(
        description="Phase 1: AI 信息萃取 — 从亚马逊产品页 HTML 提取结构化产品属性",
    )
    parser.add_argument(
        "--html-dir",
        required=True,
        help="HTML 存档目录路径（如 html/）",
    )
    parser.add_argument(
        "--db",
        required=True,
        help="SQLite 数据库文件路径（如 products.db）",
    )
    return parser.parse_args(argv)


def main() -> None:
    """CLI 主入口：遍历 HTML 目录 → AI 萃取 → 写入数据库。"""
    import sqlite3

    from db import init_db

    args = _parse_cli_args()

    html_dir = args.html_dir
    db_path = args.db

    # 从 HTML 目录发现 ASIN
    if not os.path.isdir(html_dir):
        print(f"错误: HTML 目录不存在: {html_dir}")
        return

    asins: list[str] = []
    for filename in sorted(os.listdir(html_dir)):
        if filename.endswith(".html"):
            asins.append(filename[:-5])  # 去掉 .html 后缀

    if not asins:
        print(f"HTML 目录中没有 .html 文件: {html_dir}")
        return

    print(f"发现 {len(asins)} 个 HTML 文件")

    # 连接数据库
    db = sqlite3.connect(db_path)
    init_db(db)

    # 创建 extractor
    try:
        extractor = create_extractor()
    except ValueError as e:
        print(f"错误: {e}")
        db.close()
        return

    def progress(current: int, total: int) -> None:
        print(f"  萃取进度: {current}/{total}")

    print(f"开始 AI 信息萃取...")
    results = run_phase1(
        asins=asins,
        html_dir=html_dir,
        db=db,
        extractor=extractor,
        progress_callback=progress,
    )

    # 报告结果
    success = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]

    print(f"\n完成！成功: {len(success)}, 失败: {len(errors)}")
    for e in errors:
        print(f"  - {e['asin']}: {e['error']}")

    db.close()


if __name__ == "__main__":
    main()
