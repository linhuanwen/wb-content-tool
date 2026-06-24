"""
测试 config.py 的行为：从 .env 读取配置，提供合理默认值。

原则：只测公共接口 (from config import settings)，不测实现细节。
"""

import os
import sys

import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _isolate_from_dotenv(monkeypatch, *env_vars: str) -> None:
    """阻止 load_dotenv() 加载真实 .env，并清除指定环境变量。

    config.py 在模块导入时会自动调用 load_dotenv() 将 .env 文件中的配置
    加载到 os.environ 中。这会导致测试默认值的用例读到真实 .env 值而失败。
    本函数 mock 掉 load_dotenv，并删除指定 env vars，确保 Settings() 读到代码默认值。
    """
    import sys as _sys
    import dotenv as _dotenv
    monkeypatch.setattr(_dotenv, "load_dotenv", lambda *a, **kw: True)
    for v in env_vars:
        monkeypatch.delenv(v, raising=False)
    # 清除 config 模块缓存，确保重新导入时使用 mock 后的 load_dotenv
    _sys.modules.pop("config", None)


class TestConfigDefaults:
    """当没有 .env 文件且未设置环境变量时，config 提供合理默认值。"""

    # 所有测试都需要禁止 load_dotenv() 加载真实 .env 文件，
    # 并清除可能残留在 os.environ 中的对应变量。
    # 否则 Settings() 构造时会读到真实 .env 值而非代码默认值。

    def test_api_provider_default(self, monkeypatch):
        """默认 AI 服务商为 openai"""
        _isolate_from_dotenv(monkeypatch, "TRANSLATE_API_PROVIDER")
        from config import Settings
        s = Settings()
        assert s.translate_api_provider == "openai"

    def test_api_key_default_empty(self, monkeypatch):
        """API Key 默认为空字符串（安全起见，不设默认 Key）"""
        _isolate_from_dotenv(monkeypatch, "TRANSLATE_API_KEY")
        from config import Settings
        s = Settings()
        assert s.translate_api_key == ""

    def test_api_base_url_default_empty(self, monkeypatch):
        """API Base URL 默认为空（使用各服务商默认地址）"""
        _isolate_from_dotenv(monkeypatch, "TRANSLATE_API_BASE_URL")
        from config import Settings
        s = Settings()
        assert s.translate_api_base_url == ""

    def test_model_default(self, monkeypatch):
        """默认翻译模型为 gpt-4o"""
        _isolate_from_dotenv(monkeypatch, "TRANSLATE_MODEL")
        from config import Settings
        s = Settings()
        assert s.translate_model == "gpt-4o"

    def test_crawler_headless_default(self, monkeypatch):
        """默认启用无头模式"""
        _isolate_from_dotenv(monkeypatch, "CRAWLER_HEADLESS")
        from config import Settings
        s = Settings()
        assert s.crawler_headless is True

    def test_crawler_delay_default(self, monkeypatch):
        """默认爬虫延迟为 5 秒"""
        _isolate_from_dotenv(monkeypatch, "CRAWLER_DELAY")
        from config import Settings
        s = Settings()
        assert s.crawler_delay == 5


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

    def test_r2_access_key_id_default_empty(self, monkeypatch):
        _isolate_from_dotenv(monkeypatch, "R2_ACCESS_KEY_ID")
        from config import Settings
        s = Settings()
        assert s.r2_access_key_id == ""

    def test_r2_secret_access_key_default_empty(self, monkeypatch):
        _isolate_from_dotenv(monkeypatch, "R2_SECRET_ACCESS_KEY")
        from config import Settings
        s = Settings()
        assert s.r2_secret_access_key == ""

    def test_r2_account_id_default_empty(self, monkeypatch):
        _isolate_from_dotenv(monkeypatch, "R2_ACCOUNT_ID")
        from config import Settings
        s = Settings()
        assert s.r2_account_id == ""

    def test_r2_bucket_default(self, monkeypatch):
        _isolate_from_dotenv(monkeypatch, "R2_BUCKET")
        from config import Settings
        s = Settings()
        assert s.r2_bucket == "wb-product-images"

    def test_r2_public_domain_default_empty(self, monkeypatch):
        _isolate_from_dotenv(monkeypatch, "R2_PUBLIC_DOMAIN")
        from config import Settings
        s = Settings()
        assert s.r2_public_domain == ""

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


# ============================================================
# Phase 1/2 拆分配置
# ============================================================


class TestPhase1ConfigDefaults:
    """Phase 1 配置默认值 — 未设置 PHASE1_* 时 fallback 到旧 TRANSLATE_* 值。"""

    def test_phase1_provider_default(self, monkeypatch):
        """PHASE1_API_PROVIDER 默认等于 TRANSLATE_API_PROVIDER（openai）"""
        # 显式覆盖掉可能的 .env 值，确保测试环境干净
        monkeypatch.setenv("TRANSLATE_API_PROVIDER", "openai")
        monkeypatch.delenv("PHASE1_API_PROVIDER", raising=False)
        from config import Settings
        s = Settings()
        assert s.phase1_api_provider == "openai"

    def test_phase1_api_key_default_empty(self, monkeypatch):
        """PHASE1_API_KEY 默认为空"""
        monkeypatch.setenv("TRANSLATE_API_KEY", "")
        monkeypatch.delenv("PHASE1_API_KEY", raising=False)
        from config import Settings
        s = Settings()
        assert s.phase1_api_key == ""

    def test_phase1_api_base_url_default_empty(self, monkeypatch):
        """PHASE1_API_BASE_URL 默认为空"""
        monkeypatch.setenv("TRANSLATE_API_BASE_URL", "")
        monkeypatch.delenv("PHASE1_API_BASE_URL", raising=False)
        from config import Settings
        s = Settings()
        assert s.phase1_api_base_url == ""

    def test_phase1_model_default(self, monkeypatch):
        """PHASE1_MODEL 默认等于 TRANSLATE_MODEL（gpt-4o）"""
        monkeypatch.setenv("TRANSLATE_MODEL", "gpt-4o")
        monkeypatch.delenv("PHASE1_MODEL", raising=False)
        from config import Settings
        s = Settings()
        assert s.phase1_model == "gpt-4o"


class TestPhase2ConfigDefaults:
    """Phase 2 配置默认值 — 未设置 PHASE2_* 时 fallback 到 Phase 1 值。"""

    def test_phase2_provider_falls_back_to_phase1(self):
        """PHASE2_API_PROVIDER 未设置时等于 PHASE1_API_PROVIDER"""
        from config import Settings
        s = Settings()
        assert s.phase2_api_provider == s.phase1_api_provider

    def test_phase2_api_key_falls_back_to_phase1(self):
        """PHASE2_API_KEY 未设置时等于 PHASE1_API_KEY"""
        from config import Settings
        s = Settings()
        assert s.phase2_api_key == s.phase1_api_key

    def test_phase2_model_falls_back_to_phase1(self):
        """PHASE2_MODEL 未设置时等于 PHASE1_MODEL"""
        from config import Settings
        s = Settings()
        assert s.phase2_model == s.phase1_model

    def test_phase2_base_url_falls_back_to_phase1(self):
        """PHASE2_API_BASE_URL 未设置时等于 PHASE1_API_BASE_URL"""
        from config import Settings
        s = Settings()
        assert s.phase2_api_base_url == s.phase1_api_base_url


class TestPhase1ConfigFromEnv:
    """显式设置 PHASE1_* 环境变量时，覆盖 TRANSLATE_* 的默认值。"""

    def test_phase1_independent_from_translate(self, monkeypatch):
        """PHASE1_* 设置后独立于 TRANSLATE_*"""
        monkeypatch.setenv("TRANSLATE_API_PROVIDER", "openai")
        monkeypatch.setenv("TRANSLATE_API_KEY", "sk-old")
        monkeypatch.setenv("TRANSLATE_MODEL", "gpt-4o")

        monkeypatch.setenv("PHASE1_API_PROVIDER", "deepseek")
        monkeypatch.setenv("PHASE1_API_KEY", "sk-phase1")
        monkeypatch.setenv("PHASE1_MODEL", "deepseek-chat")

        from config import Settings
        s = Settings()
        assert s.phase1_api_provider == "deepseek"
        assert s.phase1_api_key == "sk-phase1"
        assert s.phase1_model == "deepseek-chat"
        # 旧配置应保持独立
        assert s.translate_api_provider == "openai"

    def test_phase1_falls_back_to_translate_when_not_set(self, monkeypatch):
        """PHASE1_* 未设置时回退到 TRANSLATE_*（向后兼容）"""
        monkeypatch.setenv("TRANSLATE_API_PROVIDER", "anthropic")
        monkeypatch.setenv("TRANSLATE_API_KEY", "sk-legacy")
        monkeypatch.setenv("TRANSLATE_API_BASE_URL", "https://legacy.api.com")
        monkeypatch.setenv("TRANSLATE_MODEL", "claude-opus-4-8")
        # 不设置 PHASE1_*

        from config import Settings
        s = Settings()
        assert s.phase1_api_provider == "anthropic"
        assert s.phase1_api_key == "sk-legacy"
        assert s.phase1_api_base_url == "https://legacy.api.com"
        assert s.phase1_model == "claude-opus-4-8"


class TestPhase2ConfigFromEnv:
    """Phase 2 可独立配置，也可 fallback 到 Phase 1。"""

    def test_phase2_independent_from_phase1(self, monkeypatch):
        """PHASE2_* 显式设置后独立于 PHASE1_*"""
        monkeypatch.setenv("PHASE1_API_PROVIDER", "deepseek")
        monkeypatch.setenv("PHASE1_MODEL", "deepseek-chat")

        monkeypatch.setenv("PHASE2_API_PROVIDER", "anthropic")
        monkeypatch.setenv("PHASE2_MODEL", "claude-opus-4-8")

        from config import Settings
        s = Settings()
        assert s.phase2_api_provider == "anthropic"
        assert s.phase2_model == "claude-opus-4-8"
        # Phase 1 保持不变
        assert s.phase1_api_provider == "deepseek"

    def test_phase2_falls_back_to_phase1_when_not_set(self, monkeypatch):
        """PHASE2_* 未设置时回退到 PHASE1_*"""
        monkeypatch.setenv("PHASE1_API_PROVIDER", "deepseek")
        monkeypatch.setenv("PHASE1_API_KEY", "sk-phase1-key")
        monkeypatch.setenv("PHASE1_MODEL", "deepseek-chat")
        monkeypatch.setenv("PHASE1_API_BASE_URL", "https://phase1.api.com")
        # 不设置 PHASE2_*

        from config import Settings
        s = Settings()
        assert s.phase2_api_provider == "deepseek"
        assert s.phase2_api_key == "sk-phase1-key"
        assert s.phase2_model == "deepseek-chat"
        assert s.phase2_api_base_url == "https://phase1.api.com"

    def test_phase2_partial_override(self, monkeypatch):
        """只覆盖 Phase 2 部分字段，其余 fallback"""
        monkeypatch.setenv("PHASE1_API_PROVIDER", "deepseek")
        monkeypatch.setenv("PHASE1_API_KEY", "sk-phase1")
        monkeypatch.setenv("PHASE1_MODEL", "deepseek-chat")

        # 仅覆盖 Phase 2 的 provider 和 model
        monkeypatch.setenv("PHASE2_API_PROVIDER", "anthropic")
        monkeypatch.setenv("PHASE2_MODEL", "claude-sonnet-4-6")
        # 不设置 PHASE2_API_KEY → 应 fallback 到 PHASE1_API_KEY

        from config import Settings
        s = Settings()
        assert s.phase2_api_provider == "anthropic"
        assert s.phase2_model == "claude-sonnet-4-6"
        assert s.phase2_api_key == "sk-phase1"  # fallback


class TestSavePhaseConfigsToEnv:
    """将 Phase 1/2 配置保存到 .env 文件。"""

    def test_save_phase1_config_to_dotenv(self, tmp_path):
        """写入 Phase 1 配置到 .env，保留其他已有配置"""
        env_path = tmp_path / ".env"
        env_path.write_text(
            "TRANSLATE_API_PROVIDER=openai\n"
            "TRANSLATE_API_KEY=old-key\n",
            encoding="utf-8",
        )

        from config import Settings

        s = Settings()
        s.save_to_env(
            phase1_provider="deepseek",
            phase1_api_key="sk-p1",
            phase1_base_url="https://p1.api.com",
            phase1_model="deepseek-chat",
            _env_path=str(env_path),
        )

        content = env_path.read_text(encoding="utf-8")
        assert "PHASE1_API_PROVIDER=deepseek" in content
        assert "PHASE1_API_KEY=sk-p1" in content
        assert "PHASE1_API_BASE_URL=https://p1.api.com" in content
        assert "PHASE1_MODEL=deepseek-chat" in content
        # 旧配置应保留
        assert "TRANSLATE_API_PROVIDER=openai" in content

    def test_save_phase2_config_to_dotenv(self, tmp_path):
        """写入 Phase 2 独立配置到 .env"""
        env_path = tmp_path / ".env"
        env_path.write_text("", encoding="utf-8")

        from config import Settings

        s = Settings()
        s.save_to_env(
            phase2_provider="anthropic",
            phase2_api_key="sk-p2",
            phase2_base_url="",
            phase2_model="claude-opus-4-8",
            _env_path=str(env_path),
        )

        content = env_path.read_text(encoding="utf-8")
        assert "PHASE2_API_PROVIDER=anthropic" in content
        assert "PHASE2_API_KEY=sk-p2" in content
        assert "PHASE2_MODEL=claude-opus-4-8" in content
        # 空 base_url 不应写入
        assert "PHASE2_API_BASE_URL" not in content

    def test_save_both_phases_together(self, tmp_path):
        """同时保存 Phase 1 和 Phase 2 配置"""
        env_path = tmp_path / ".env"
        env_path.write_text("CRAWLER_MODE=playwright\n", encoding="utf-8")

        from config import Settings

        s = Settings()
        s.save_to_env(
            phase1_provider="deepseek",
            phase1_api_key="sk-p1",
            phase1_base_url="https://p1.api.com",
            phase1_model="deepseek-chat",
            phase2_provider="anthropic",
            phase2_api_key="sk-p2",
            phase2_model="claude-sonnet-4-6",
            _env_path=str(env_path),
        )

        content = env_path.read_text(encoding="utf-8")
        assert "PHASE1_API_PROVIDER=deepseek" in content
        assert "PHASE1_API_KEY=sk-p1" in content
        assert "PHASE2_API_PROVIDER=anthropic" in content
        assert "PHASE2_API_KEY=sk-p2" in content
        assert "CRAWLER_MODE=playwright" in content


class TestTranslateConfigBackwardCompatibility:
    """旧的 TRANSLATE_* 配置仍可正常使用（向后兼容）。"""

    def test_translate_config_still_works(self, monkeypatch):
        """设置 TRANSLATE_* 后，旧的 settings.translate_* 属性仍能读取"""
        monkeypatch.setenv("TRANSLATE_API_PROVIDER", "deepseek")
        monkeypatch.setenv("TRANSLATE_API_KEY", "sk-test")
        monkeypatch.setenv("TRANSLATE_MODEL", "deepseek-chat")

        from config import Settings
        s = Settings()
        # 旧属性仍可访问
        assert s.translate_api_provider == "deepseek"
        assert s.translate_api_key == "sk-test"
        assert s.translate_model == "deepseek-chat"

    def test_phase1_uses_translate_as_fallback_integration(self, monkeypatch):
        """集成测试：仅设置旧 TRANSLATE_*，Phase 1 和 Phase 2 都能正常工作"""
        monkeypatch.setenv("TRANSLATE_API_PROVIDER", "custom")
        monkeypatch.setenv("TRANSLATE_API_KEY", "sk-integration")
        monkeypatch.setenv("TRANSLATE_API_BASE_URL", "https://custom.api.com")
        monkeypatch.setenv("TRANSLATE_MODEL", "custom-model")

        from config import Settings
        s = Settings()

        # Phase 1 读取旧配置
        assert s.phase1_api_provider == "custom"
        assert s.phase1_api_key == "sk-integration"
        assert s.phase1_api_base_url == "https://custom.api.com"
        assert s.phase1_model == "custom-model"

        # Phase 2 fallback 到 Phase 1（Phase 1 又 fallback 到 TRANSLATE）
        assert s.phase2_api_provider == "custom"
        assert s.phase2_api_key == "sk-integration"
        assert s.phase2_model == "custom-model"
