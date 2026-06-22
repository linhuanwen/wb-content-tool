"""
配置管理模块 — 从 .env 文件和环境变量读取配置。

通过 Settings 类暴露所有配置项，模块级 settings 实例供全局使用：
    from config import settings
    print(settings.translate_api_provider)
"""

import os

from dotenv import load_dotenv

# 加载 .env 文件（不覆盖已有的环境变量）
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
        self.translate_api_provider = _str(env("TRANSLATE_API_PROVIDER"), "openai")
        self.translate_api_key = _str(env("TRANSLATE_API_KEY"), "")
        self.translate_api_base_url = _str(env("TRANSLATE_API_BASE_URL"), "")
        self.translate_model = _str(env("TRANSLATE_MODEL"), "gpt-4o")
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
        _env_path: str | None = None,
    ) -> None:
        """将翻译 API 和爬虫配置写入 .env 文件。

        保留文件中已有的其他配置行不变，只更新对应的配置行。
        若某参数为空字符串，则跳过写入（保留现有值）。
        若 .env 文件不存在则创建。

        Args:
            provider: AI 服务商名称。
            api_key: API 密钥。
            base_url: API 地址。
            model: 模型名称。
            crawler_mode: 爬虫模式 (playwright / scraperapi)。
            scraperapi_key: ScraperAPI Key。
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


# 模块级便捷实例
settings = Settings()
