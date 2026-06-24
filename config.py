"""
配置管理模块 — 从 .env 文件和环境变量读取配置。

通过 Settings 类暴露所有配置项，模块级 settings 实例供全局使用：
    from config import settings
    print(settings.translate_api_provider)
"""

import os

from dotenv import load_dotenv

# 加载 .env 文件（不覆盖已有的环境变量）
# 若 .env 文件不存在，自动以默认值创建
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if not os.path.isfile(_ENV_PATH):
    _DEFAULT_ENV = """\
# ============================================================
# WB Content Tool — 环境变量配置
# 在 Web 界面侧边栏修改配置后点击"保存配置"即可更新此文件
# ============================================================

# ---- Phase 1 API（信息萃取 + 传统管线图片翻译）----
PHASE1_API_PROVIDER=deepseek
PHASE1_API_KEY=
PHASE1_API_BASE_URL=
PHASE1_MODEL=deepseek-v4-pro

# ---- Phase 2 API（俄语文案生成，留空则复用 Phase 1 配置）----
PHASE2_API_PROVIDER=
PHASE2_API_KEY=
PHASE2_API_BASE_URL=
PHASE2_MODEL=

# ---- 爬虫配置 ----
CRAWLER_MODE=playwright
CRAWLER_HEADLESS=true
CRAWLER_DELAY=5
SCRAPERAPI_KEY=

# ---- R2 图片存储 ----
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_ACCOUNT_ID=
R2_BUCKET=wb-product-images
R2_PUBLIC_DOMAIN=

# ---- 图片处理 ----
IMAGE_LOCAL_PATH=images
IMAGE_RESIZE_MODE=pad
IMAGE_OUTPUT_SIZE=900x1200
IMAGE_REPAIR_PROVIDER=replicate
IMAGE_REPAIR_API_KEY=
IMAGE_REPAIR_MODEL=tencentarc/gfpgan

# ---- 图片生成（Gemini / 中转站）----
IMAGE_GEN_PROVIDER=gemini
IMAGE_GEN_API_KEY=
IMAGE_GEN_BASE_URL=
IMAGE_GEN_MODEL=
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash-image
GEMINI_PROXY=http://127.0.0.1:10808

# ---- 重试与超时 ----
STEP_MAX_RETRIES=3
STEP_TIMEOUT=60
RETRY_BACKOFF_BASE=2

# ---- 旧配置名（向后兼容，新项目请使用上方 PHASE1_* 配置）----
TRANSLATE_API_PROVIDER=deepseek
TRANSLATE_API_KEY=
TRANSLATE_API_BASE_URL=
TRANSLATE_MODEL=deepseek-v4-pro
"""
    with open(_ENV_PATH, "w", encoding="utf-8") as _f:
        _f.write(_DEFAULT_ENV)

load_dotenv()


def _bool(value: str | None, default: bool) -> bool:
    """将字符串环境变量值解析为布尔值。"""
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes")


def _int(value: str | None, default: int) -> int:
    """将字符串环境变量值解析为整数。"""
    if value is None:
        return default
    return int(value)


def _str(value: str | None, default: str) -> str:
    """返回环境变量值，若未设置则返回默认值。"""
    return default if value is None else value


class Settings:
    """应用配置，从环境变量 / .env 文件读取。

    每个属性对应一个大写下划线命名的环境变量，例如：
        translate_api_provider ← TRANSLATE_API_PROVIDER
    """

    def __init__(self) -> None:
        env = os.getenv
        # 旧配置名（向后兼容）
        self.translate_api_provider = _str(env("TRANSLATE_API_PROVIDER"), "openai")
        self.translate_api_key = _str(env("TRANSLATE_API_KEY"), "")
        self.translate_api_base_url = _str(env("TRANSLATE_API_BASE_URL"), "")
        self.translate_model = _str(env("TRANSLATE_MODEL"), "gpt-4o")
        # Phase 1 配置（未设置时 fallback 到旧 TRANSLATE_* 配置）
        self.phase1_api_provider = _str(
            env("PHASE1_API_PROVIDER"), self.translate_api_provider
        )
        self.phase1_api_key = _str(
            env("PHASE1_API_KEY"), self.translate_api_key
        )
        self.phase1_api_base_url = _str(
            env("PHASE1_API_BASE_URL"), self.translate_api_base_url
        )
        self.phase1_model = _str(
            env("PHASE1_MODEL"), self.translate_model
        )
        # Phase 2 配置（未设置时 fallback 到 Phase 1 值）
        self.phase2_api_provider = _str(
            env("PHASE2_API_PROVIDER"), self.phase1_api_provider
        )
        self.phase2_api_key = _str(
            env("PHASE2_API_KEY"), self.phase1_api_key
        )
        self.phase2_api_base_url = _str(
            env("PHASE2_API_BASE_URL"), self.phase1_api_base_url
        )
        self.phase2_model = _str(
            env("PHASE2_MODEL"), self.phase1_model
        )
        # 爬虫
        self.crawler_mode = _str(env("CRAWLER_MODE"), "playwright")
        self.crawler_headless = _bool(env("CRAWLER_HEADLESS"), True)
        self.crawler_delay = _int(env("CRAWLER_DELAY"), 5)
        self.scraperapi_key = _str(env("SCRAPERAPI_KEY"), "")
        # R2 对象存储
        self.r2_access_key_id = _str(env("R2_ACCESS_KEY_ID"), "")
        self.r2_secret_access_key = _str(env("R2_SECRET_ACCESS_KEY"), "")
        self.r2_account_id = _str(env("R2_ACCOUNT_ID"), "")
        self.r2_bucket = _str(env("R2_BUCKET"), "wb-product-images")
        self.r2_public_domain = _str(env("R2_PUBLIC_DOMAIN"), "")
        # 图片处理
        self.image_local_path = _str(env("IMAGE_LOCAL_PATH"), "images")
        self.image_resize_mode = _str(env("IMAGE_RESIZE_MODE"), "pad")
        self.image_output_size = _str(env("IMAGE_OUTPUT_SIZE"), "900x1200")
        self.image_repair_provider = _str(env("IMAGE_REPAIR_PROVIDER"), "replicate")
        self.image_repair_api_key = _str(env("IMAGE_REPAIR_API_KEY"), "")
        self.image_repair_model = _str(env("IMAGE_REPAIR_MODEL"), "tencentarc/gfpgan")
        # 图片生成提供商选择
        # "gemini" — Google Gemini 2.5 Flash Image（原生图片生成）
        # "openai_compatible" — OpenAI 兼容接口（中转站 gpt-image-2 等）
        self.image_gen_provider = _str(env("IMAGE_GEN_PROVIDER"), "gemini")
        self.image_gen_api_key = _str(env("IMAGE_GEN_API_KEY"), "")
        self.image_gen_base_url = _str(env("IMAGE_GEN_BASE_URL"), "")
        self.image_gen_model = _str(env("IMAGE_GEN_MODEL"), "")
        # 图片生成模式："card_design"（卡片设计）或 "translate"（图片翻译）
        self.image_gen_mode = _str(env("IMAGE_GEN_MODE"), "card_design")
        # Gemini 专用（向后兼容）
        self.gemini_api_key = _str(env("GEMINI_API_KEY"), "")
        self.gemini_model = _str(env("GEMINI_MODEL"), "gemini-2.5-flash-image")
        self.gemini_proxy = _str(env("GEMINI_PROXY"), "http://127.0.0.1:10808")
        # 重试与超时
        self.step_max_retries = _int(env("STEP_MAX_RETRIES"), 3)
        self.step_timeout = _int(env("STEP_TIMEOUT"), 60)
        self.retry_backoff_base = _int(env("RETRY_BACKOFF_BASE"), 2)

    def save_to_env(
        self,
        provider: str = "",
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        *,
        crawler_mode: str = "",
        scraperapi_key: str = "",
        phase1_provider: str = "",
        phase1_api_key: str = "",
        phase1_base_url: str = "",
        phase1_model: str = "",
        phase2_provider: str = "",
        phase2_api_key: str = "",
        phase2_base_url: str = "",
        phase2_model: str = "",
        image_gen_provider: str = "",
        image_gen_api_key: str = "",
        image_gen_base_url: str = "",
        image_gen_model: str = "",
        image_gen_mode: str = "",
        gemini_api_key: str = "",
        gemini_model: str = "",
        gemini_proxy: str = "",
        _env_path: str | None = None,
    ) -> None:
        """将翻译 API、Phase 1/2、爬虫和 Gemini 配置写入 .env 文件。

        保留文件中已有的其他配置行不变，只更新对应的配置行。
        若某参数为空字符串，则跳过写入（保留现有值）。
        若 .env 文件不存在则创建。

        Args:
            provider: AI 服务商名称（旧 TRANSLATE_* 配置）。
            api_key: API 密钥（旧配置）。
            base_url: API 地址（旧配置）。
            model: 模型名称（旧配置）。
            crawler_mode: 爬虫模式 (playwright / scraperapi)。
            scraperapi_key: ScraperAPI Key。
            phase1_provider: Phase 1 服务商名称。
            phase1_api_key: Phase 1 API 密钥。
            phase1_base_url: Phase 1 API 地址。
            phase1_model: Phase 1 模型名称。
            phase2_provider: Phase 2 服务商名称。
            phase2_api_key: Phase 2 API 密钥。
            phase2_base_url: Phase 2 API 地址。
            phase2_model: Phase 2 模型名称。
            gemini_api_key: Gemini API 密钥。
            gemini_model: Gemini 图片生成模型。
            _env_path: 测试注入用，指定 .env 文件路径。
        """
        import re

        env_path = _env_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".env"
        )

        # 要写入的新值（跳过空值）
        candidates = {
            "TRANSLATE_API_PROVIDER": provider.strip(),
            "TRANSLATE_API_KEY": api_key.strip(),
            "TRANSLATE_API_BASE_URL": base_url.strip(),
            "TRANSLATE_MODEL": model.strip(),
            "CRAWLER_MODE": crawler_mode.strip(),
            "SCRAPERAPI_KEY": scraperapi_key.strip(),
            "PHASE1_API_PROVIDER": phase1_provider.strip(),
            "PHASE1_API_KEY": phase1_api_key.strip(),
            "PHASE1_API_BASE_URL": phase1_base_url.strip(),
            "PHASE1_MODEL": phase1_model.strip(),
            "PHASE2_API_PROVIDER": phase2_provider.strip(),
            "PHASE2_API_KEY": phase2_api_key.strip(),
            "PHASE2_API_BASE_URL": phase2_base_url.strip(),
            "PHASE2_MODEL": phase2_model.strip(),
            "GEMINI_API_KEY": gemini_api_key.strip(),
            "GEMINI_MODEL": gemini_model.strip(),
            "GEMINI_PROXY": gemini_proxy.strip(),
            "IMAGE_GEN_PROVIDER": image_gen_provider.strip(),
            "IMAGE_GEN_API_KEY": image_gen_api_key.strip(),
            "IMAGE_GEN_BASE_URL": image_gen_base_url.strip(),
            "IMAGE_GEN_MODEL": image_gen_model.strip(),
            "IMAGE_GEN_MODE": image_gen_mode.strip(),
        }
        new_values = {k: v for k, v in candidates.items() if v}

        # 读取已有内容
        if os.path.isfile(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        else:
            lines = []

        # 更新或追加配置行
        updated_keys: set[str] = set()
        result_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            matched = False
            for key, value in new_values.items():
                if re.match(rf"^{key}\s*=", stripped):
                    result_lines.append(f"{key}={value}\n")
                    updated_keys.add(key)
                    matched = True
                    break
            if not matched:
                result_lines.append(line)

        # 追加未出现在原文件中的键
        for key in new_values:
            if key not in updated_keys:
                result_lines.append(f"{key}={new_values[key]}\n")

        # 写入文件
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(result_lines)

        # 同时更新当前实例的属性值
        for key, value in new_values.items():
            if key == "TRANSLATE_API_PROVIDER":
                self.translate_api_provider = value
            elif key == "TRANSLATE_API_KEY":
                self.translate_api_key = value
            elif key == "TRANSLATE_API_BASE_URL":
                self.translate_api_base_url = value
            elif key == "TRANSLATE_MODEL":
                self.translate_model = value
            elif key == "CRAWLER_MODE":
                self.crawler_mode = value
            elif key == "SCRAPERAPI_KEY":
                self.scraperapi_key = value
            elif key == "PHASE1_API_PROVIDER":
                self.phase1_api_provider = value
            elif key == "PHASE1_API_KEY":
                self.phase1_api_key = value
            elif key == "PHASE1_API_BASE_URL":
                self.phase1_api_base_url = value
            elif key == "PHASE1_MODEL":
                self.phase1_model = value
            elif key == "PHASE2_API_PROVIDER":
                self.phase2_api_provider = value
            elif key == "PHASE2_API_KEY":
                self.phase2_api_key = value
            elif key == "PHASE2_API_BASE_URL":
                self.phase2_api_base_url = value
            elif key == "PHASE2_MODEL":
                self.phase2_model = value
            elif key == "GEMINI_API_KEY":
                self.gemini_api_key = value
            elif key == "GEMINI_MODEL":
                self.gemini_model = value
            elif key == "GEMINI_PROXY":
                self.gemini_proxy = value
            elif key == "IMAGE_GEN_PROVIDER":
                self.image_gen_provider = value
            elif key == "IMAGE_GEN_API_KEY":
                self.image_gen_api_key = value
            elif key == "IMAGE_GEN_BASE_URL":
                self.image_gen_base_url = value
            elif key == "IMAGE_GEN_MODEL":
                self.image_gen_model = value
            elif key == "IMAGE_GEN_MODE":
                self.image_gen_mode = value


# 模块级便捷实例
settings = Settings()
