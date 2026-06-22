"""
亚马逊产品信息提取器。

从亚马逊产品页 HTML 提取结构化数据：标题、图片 URL、详情。
纯函数，不依赖网络——接收 HTML 字符串，返回字典。
"""

import re

from bs4 import BeautifulSoup

# 匹配亚马逊图片 URL 中的尺寸后缀，例如：
#   ._AC_US40_  ._SX38_SY50_  ._AC_SL1500_  ._SX425_
#   ._SX425_CR,0,0,425,425_SY425_（带裁剪参数）
# 去掉这些后缀即可获得原始分辨率图片。
_SIZE_SUFFIX_RE = re.compile(r"\._[A-Z0-9,_]+_")


def _normalize_image_url(url: str) -> str:
    """移除亚马逊图片 URL 的尺寸限制后缀，返回原始分辨率 URL。

    Args:
        url: 原始图片 URL（可能带尺寸后缀）。

    Returns:
        去除尺寸后缀后的 URL。若 URL 不含尺寸后缀则原样返回。
    """
    return _SIZE_SUFFIX_RE.sub("", url)


def extract_product_info(html: str, asin: str) -> dict:
    """从亚马逊产品页 HTML 中提取产品信息。

    Args:
        html: 产品页 HTML 源码。
        asin: 亚马逊 ASIN 标识码。

    Returns:
        包含 asin, 标题, 图片url, 详情 四个键的字典。
        所有缺失字段返回空字符串。
    """
    soup = BeautifulSoup(html, "lxml")

    return {
        "asin": asin,
        "标题": _extract_title(soup),
        "图片url": _extract_images(soup),
        "详情": _extract_details(soup),
    }


def _extract_title(soup: BeautifulSoup) -> str:
    """提取产品标题。"""
    el = soup.select_one("#productTitle")
    if el is None:
        return ""
    return el.get_text(strip=True)


def _extract_images(soup: BeautifulSoup) -> str:
    """提取产品图片 URL，去重后用分号连接。

    优先使用 data-old-hires 属性（高分辨率），
    否则从 src 属性提取，并移除亚马逊的尺寸后缀以获得原始分辨率图片。
    """
    container = soup.select_one("#altImages")
    if container is None:
        return ""

    seen: set[str] = set()
    urls: list[str] = []
    for img in container.find_all("img"):
        # data-old-hires 通常包含高分辨率原图 URL
        src = img.get("data-old-hires", "") or img.get("src", "")
        if not src:
            continue
        # 移除亚马逊的尺寸后缀 → 获取原始分辨率
        normalized = _normalize_image_url(src)
        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)

    return ";".join(urls)


def _extract_details(soup: BeautifulSoup) -> str:
    """提取产品详情。

    合并 #feature-bullets 和 #productDescription 的内容。
    """
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

    return "\n".join(parts)
