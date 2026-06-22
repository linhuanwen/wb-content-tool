"""
测试 config.py 的行为：从 .env 读取配置，提供合理默认值。

原则：只测公共接口 (from config import settings)，不测实现细节。
"""

import os
import sys

import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestConfigDefaults:
    """当没有 .env 文件且未设置环境变量时，config 提供合理默认值。"""

    def test_api_provider_default(self):
        """默认 AI 服务商为 openai"""
        from config import settings
        assert settings.translate_api_provider == "openai"

    def test_api_key_default_empty(self):
        """API Key 默认为空字符串（安全起见，不设默认 Key）"""
        from config import settings
        assert settings.translate_api_key == ""

    def test_api_base_url_default_empty(self):
        """API Base URL 默认为空（使用各服务商默认地址）"""
        from config import settings
        assert settings.translate_api_base_url == ""

    def test_model_default(self):
        """默认翻译模型为 gpt-4o"""
        from config import settings
        assert settings.translate_model == "gpt-4o"

    def test_crawler_headless_default(self):
        """默认启用无头模式"""
        from config import settings
        assert settings.crawler_headless is True

    def test_crawler_delay_default(self):
        """默认爬虫延迟为 5 秒"""
        from config import settings
        assert settings.crawler_delay == 5


class TestConfigFromEnv:
    """当设置环境变量时，config 应读取环境变量值而非默认值。"""

    def test_read_api_provider_from_env(self, monkeypatch):
        """从环境变量读取 TRANSLATE_API_PROVIDER"""
        monkeypatch.setenv("TRANSLATE_API_PROVIDER", "anthropic")
        from config import Settings
        s = Settings()
        assert s.translate_api_provider == "anthropic"

    def test_read_api_key_from_env(self, monkeypatch):
        """从环境变量读取 TRANSLATE_API_KEY"""
        monkeypatch.setenv("TRANSLATE_API_KEY", "sk-test-123")
        from config import Settings
        s = Settings()
        assert s.translate_api_key == "sk-test-123"

    def test_read_api_base_url_from_env(self, monkeypatch):
        """从环境变量读取 TRANSLATE_API_BASE_URL"""
        monkeypatch.setenv("TRANSLATE_API_BASE_URL", "https://api.example.com")
        from config import Settings
        s = Settings()
        assert s.translate_api_base_url == "https://api.example.com"

    def test_read_model_from_env(self, monkeypatch):
        """从环境变量读取 TRANSLATE_MODEL"""
        monkeypatch.setenv("TRANSLATE_MODEL", "claude-opus-4-8")
        from config import Settings
        s = Settings()
        assert s.translate_model == "claude-opus-4-8"

    def test_read_crawler_headless_false(self, monkeypatch):
        """CRAWLER_HEADLESS=false 应被正确解析为布尔值 False"""
        monkeypatch.setenv("CRAWLER_HEADLESS", "false")
        from config import Settings
        s = Settings()
        assert s.crawler_headless is False

    def test_read_crawler_headless_true(self, monkeypatch):
        """CRAWLER_HEADLESS=1 应被正确解析为布尔值 True"""
        monkeypatch.setenv("CRAWLER_HEADLESS", "1")
        from config import Settings
        s = Settings()
        assert s.crawler_headless is True

    def test_read_crawler_delay_from_env(self, monkeypatch):
        """从环境变量读取 CRAWLER_DELAY 并转为整数"""
        monkeypatch.setenv("CRAWLER_DELAY", "10")
        from config import Settings
        s = Settings()
        assert s.crawler_delay == 10
        assert isinstance(s.crawler_delay, int)


class TestSaveToEnv:
    """将 API 配置保存到 .env 文件。"""

    def test_save_writes_translation_config_to_dotenv(self, tmp_path):
        """写入翻译配置到 .env 文件，保留其他已有配置"""
        import os

        # 创建临时 .env 文件（模拟已有爬虫配置）
        env_path = tmp_path / ".env"
        env_path.write_text(
            "CRAWLER_HEADLESS=true\n"
            "CRAWLER_DELAY=5\n"
            "# 旧的翻译配置\n"
            "TRANSLATE_API_PROVIDER=openai\n"
            "TRANSLATE_API_KEY=old-key\n",
            encoding="utf-8",
        )

        from config import Settings

        s = Settings()
        # 注入临时 .env 路径保存
        s.save_to_env(
            provider="anthropic",
            api_key="sk-new-key",
            base_url="https://api.custom.com",
            model="claude-opus-4-8",
            _env_path=str(env_path),
        )

        # 验证写入的内容
        content = env_path.read_text(encoding="utf-8")
        assert "TRANSLATE_API_PROVIDER=anthropic" in content
        assert "TRANSLATE_API_KEY=sk-new-key" in content
        assert "TRANSLATE_API_BASE_URL=https://api.custom.com" in content
        assert "TRANSLATE_MODEL=claude-opus-4-8" in content
        # 其他配置应保留
        assert "CRAWLER_HEADLESS=true" in content
        assert "CRAWLER_DELAY=5" in content
        # 旧翻译配置应被替换
        assert "TRANSLATE_API_KEY=old-key" not in content

    def test_save_creates_dotenv_if_not_exists(self, tmp_path):
        """.env 文件不存在时创建新文件"""
        import os

        env_path = tmp_path / ".env"
        # 确保文件不存在
        assert not env_path.exists()

        from config import Settings

        s = Settings()
        s.save_to_env(
            provider="deepseek",
            api_key="sk-deepseek",
            base_url="",
            model="deepseek-chat",
            _env_path=str(env_path),
        )

        assert env_path.exists()
        content = env_path.read_text(encoding="utf-8")
        assert "TRANSLATE_API_PROVIDER=deepseek" in content
        assert "TRANSLATE_API_KEY=sk-deepseek" in content
        assert "TRANSLATE_MODEL=deepseek-chat" in content


# ============================================================
# R2 / 图片 / 重试 配置项默认值
# ============================================================


class TestR2ConfigDefaults:
    """R2 配置项默认值（.env 未设置时）。"""

    def test_r2_access_key_id_default_empty(self):
        from config import settings
        assert settings.r2_access_key_id == ""

    def test_r2_secret_access_key_default_empty(self):
        from config import settings
        assert settings.r2_secret_access_key == ""

    def test_r2_account_id_default_empty(self):
        from config import settings
        assert settings.r2_account_id == ""

    def test_r2_bucket_default(self):
        from config import settings
        assert settings.r2_bucket == "wb-product-images"

    def test_r2_public_domain_default_empty(self):
        from config import settings
        assert settings.r2_public_domain == ""

    def test_r2_config_from_env(self, monkeypatch):
        """从环境变量读取 R2 配置"""
        monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key-id")
        monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
        monkeypatch.setenv("R2_ACCOUNT_ID", "test-account")
        monkeypatch.setenv("R2_BUCKET", "my-bucket")
        monkeypatch.setenv("R2_PUBLIC_DOMAIN", "https://my.example.com")
        from config import Settings
        s = Settings()
        assert s.r2_access_key_id == "test-key-id"
        assert s.r2_secret_access_key == "test-secret"
        assert s.r2_account_id == "test-account"
        assert s.r2_bucket == "my-bucket"
        assert s.r2_public_domain == "https://my.example.com"


class TestImageConfigDefaults:
    """图片处理配置项默认值。"""

    def test_image_local_path_default(self):
        from config import settings
        assert settings.image_local_path == "images"

    def test_image_resize_mode_default(self):
        from config import settings
        assert settings.image_resize_mode == "pad"

    def test_image_output_size_default(self):
        from config import settings
        assert settings.image_output_size == "900x1200"

    def test_image_repair_provider_default(self):
        from config import settings
        assert settings.image_repair_provider == "replicate"

    def test_image_repair_api_key_default_empty(self):
        from config import settings
        assert settings.image_repair_api_key == ""

    def test_image_repair_model_default(self):
        from config import settings
        assert settings.image_repair_model == "tencentarc/gfpgan"

    def test_image_config_from_env(self, monkeypatch):
        """从环境变量读取图片处理配置"""
        monkeypatch.setenv("IMAGE_LOCAL_PATH", "output_images")
        monkeypatch.setenv("IMAGE_RESIZE_MODE", "outpainting")
        monkeypatch.setenv("IMAGE_OUTPUT_SIZE", "1200x1600")
        monkeypatch.setenv("IMAGE_REPAIR_API_KEY", "r8-test-key")
        monkeypatch.setenv("IMAGE_REPAIR_MODEL", "custom/model")
        from config import Settings
        s = Settings()
        assert s.image_local_path == "output_images"
        assert s.image_resize_mode == "outpainting"
        assert s.image_output_size == "1200x1600"
        assert s.image_repair_api_key == "r8-test-key"
        assert s.image_repair_model == "custom/model"


class TestRetryConfigDefaults:
    """重试与超时配置项默认值。"""

    def test_step_max_retries_default(self):
        from config import settings
        assert settings.step_max_retries == 3
        assert isinstance(settings.step_max_retries, int)

    def test_step_timeout_default(self):
        from config import settings
        assert settings.step_timeout == 60
        assert isinstance(settings.step_timeout, int)

    def test_retry_backoff_base_default(self):
        from config import settings
        assert settings.retry_backoff_base == 2
        assert isinstance(settings.retry_backoff_base, int)

    def test_retry_config_from_env(self, monkeypatch):
        """从环境变量读取重试配置，值应为整数"""
        monkeypatch.setenv("STEP_MAX_RETRIES", "5")
        monkeypatch.setenv("STEP_TIMEOUT", "120")
        monkeypatch.setenv("RETRY_BACKOFF_BASE", "3")
        from config import Settings
        s = Settings()
        assert s.step_max_retries == 5
        assert s.step_timeout == 120
        assert s.retry_backoff_base == 3
