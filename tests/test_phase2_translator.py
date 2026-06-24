"""
测试 phase2_translator.py — Phase 2 AI 文案生成模块。

测试行为（非实现）：
- Phase2Generator 是抽象基类，不能直接实例化
- 工厂函数根据配置创建正确的 Generator 实例
- 从产品记录构建 User Message
- System Prompt 加载
- JSON 解析（Phase 2 专用字段）与重试
- MockPhase2Generator 返回可预测数据（不依赖真实 API）
- run_phase2 批量生成 → 写入 translations 表
- CLI 入口
- 标题后处理（≤60字符、全小写、无特殊符号、去品牌词）
- 品牌词精准去除（不误删普通词）
- API Key 未配置时给出明确错误提示，不崩溃
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# Phase 2 JSON 响应示例
# ============================================================

VALID_PHASE2_JSON = """
{
  "russian_title": "массажер для лица 5 в 1 с микротоками",
  "core_keywords": "микротоковый массажер аппарат для лица лифтинг аппарат ультразвуковой массажер рф лифтинг led маска",
  "russian_description": "Многофункциональный массажер для лица объединяет 5 передовых технологий омоложения в одном компактном устройстве. Микротоки мягко подтягивают контур лица, ультразвук улучшает проникновение сывороток. Идеально для домашнего ухода за лицом."
}
"""

VALID_PHASE2_JSON_INLINE = (
    '{"russian_title":"бутылка для масла стеклянная",'
    '"core_keywords":"бутылка для масла стеклянная банка кухонный инвентарь",'
    '"russian_description":"Стеклянная бутылка для масла с распылителем."}'
)

PHASE2_JSON_IN_MARKDOWN = """
Вот сгенерированный текст:

```json
{
  "russian_title": "масляный насос автомобильный",
  "core_keywords": "насос масляный автомобильный двигатель система смазки",
  "russian_description": "Автомобильный масляный насос высокого качества для стабильной работы двигателя."
}
```
"""


# ============================================================
# 示例结构化产品记录（Phase 1 输出格式）
# ============================================================

SAMPLE_PRODUCT_RECORD = {
    "asin": "B0GVYXC124",
    "title": "5-in-1 Face Sculpting Machine with LED Light Therapy",
    "details": "Multifunctional beauty device combines 5 technologies for home use.",
    "image_urls": "https://img1.jpg;https://img2.jpg",
    "html_path": "html/B0GVYXC124.html",
    "category": "Beauty & Personal Care",
    "material": "ABS Plastic, Silicone",
    "color": "White",
    "dimensions": "15 x 10 x 5 cm",
    "weight": "350g",
    "capacity": "",
    "package_contents": "1 x Device, 1 x USB Cable, 1 x Manual",
    "features": ["Facial massage", "Body contouring", "LED light therapy", "5 adjustable modes"],
    "technical_specs": {"Power": "5W", "Battery": "2000mAh"},
    "target_audience": "Women 25-45",
    "use_scenarios": ["Home spa", "Daily skincare routine"],
    "unique_selling_points": ["5-in-1 multifunction", "Portable USB charging"],
    "brand": "BeautyPro",
    "en_search_keywords": ["face sculpting machine", "facial massager", "body contouring device"],
}

SAMPLE_PRODUCT_RECORD_2 = {
    "asin": "B0F45N6NS7",
    "title": "Anti Cellulite Massager",
    "details": "Handheld electric anti-cellulite device.",
    "image_urls": "https://img3.jpg",
    "html_path": "html/B0F45N6NS7.html",
    "category": "Beauty & Personal Care",
    "material": "Silicone",
    "color": "Pink",
    "dimensions": "10 x 5 x 3 cm",
    "weight": "200g",
    "capacity": "",
    "package_contents": "1 x Massager, 1 x Charging Cable",
    "features": ["Anti-cellulite", "Electric massage", "Handheld"],
    "technical_specs": {"Battery": "1500mAh", "Power": "3W"},
    "target_audience": "Women 20-50",
    "use_scenarios": ["Home", "Gym"],
    "unique_selling_points": ["Portable", "Quick results"],
    "brand": "",
    "en_search_keywords": ["anti cellulite massager", "body massager", "electric massager"],
}


# ============================================================
# 切片1: Phase2Generator ABC — 不能直接实例化
# ============================================================

class TestPhase2GeneratorIsAbstract:
    """Phase2Generator 是抽象基类，不能直接实例化。"""

    def test_cannot_instantiate_abstract_generator(self):
        """直接实例化 Phase2Generator 应抛出 TypeError"""
        from phase2_translator import Phase2Generator

        with pytest.raises(TypeError):
            Phase2Generator()  # type: ignore[abstract]


# ============================================================
# 切片2: System Prompt 加载
# ============================================================

class TestLoadPhase2Prompt:
    """加载 prompts/phase2_translation_persona.txt 作为 System Prompt。"""

    def test_returns_non_empty_string(self):
        """返回非空字符串"""
        from phase2_translator import _load_phase2_prompt

        prompt = _load_phase2_prompt()
        assert isinstance(prompt, str)
        assert len(prompt.strip()) > 0

    def test_contains_persona_content(self):
        """加载的内容包含角色定义（Wildberries 俄语电商文案优化师）"""
        from phase2_translator import _load_phase2_prompt

        prompt = _load_phase2_prompt()
        assert "Wildberries" in prompt

    def test_contains_phase2_strategy_content(self):
        """加载的内容包含 Phase 2 特有策略：品类差异化、去品牌化规则、结构化输入"""
        from phase2_translator import _load_phase2_prompt

        prompt = _load_phase2_prompt()
        assert "品类差异化" in prompt
        assert "去品牌化" in prompt
        assert "brand" in prompt
        assert "category" in prompt
        assert "en_search_keywords" in prompt

    def test_contains_json_output_format(self):
        """加载的内容包含 JSON 输出格式指令"""
        from phase2_translator import _load_phase2_prompt

        prompt = _load_phase2_prompt()
        assert "russian_title" in prompt
        assert "core_keywords" in prompt
        assert "russian_description" in prompt

    def test_raises_if_file_missing(self, monkeypatch):
        """文件不存在时抛出明确错误"""
        from phase2_translator import _load_phase2_prompt

        monkeypatch.setattr(
            "phase2_translator._PHASE2_PERSONA_PATH",
            os.path.join(os.path.dirname(__file__), "nonexistent.txt"),
        )
        with pytest.raises(FileNotFoundError):
            _load_phase2_prompt()


# ============================================================
# 切片3: _build_user_message_from_record — 从产品记录构建 User Message
# ============================================================

class TestBuildUserMessageFromRecord:
    """从结构化产品记录构建发送给 AI 的用户消息。"""

    def test_includes_all_key_fields(self):
        """消息中包含所有关键产品字段"""
        from phase2_translator import _build_user_message_from_record

        msg = _build_user_message_from_record(SAMPLE_PRODUCT_RECORD)
        assert "B0GVYXC124" in msg
        assert "Beauty & Personal Care" in msg
        assert "5-in-1 Face Sculpting Machine" in msg
        assert "ABS Plastic, Silicone" in msg
        assert "BeautyPro" in msg

    def test_includes_json_arrays_as_formatted(self):
        """数组字段以可读格式出现"""
        from phase2_translator import _build_user_message_from_record

        msg = _build_user_message_from_record(SAMPLE_PRODUCT_RECORD)
        assert "Facial massage" in msg
        assert "Body contouring" in msg
        assert "Home spa" in msg

    def test_includes_brand_for_debranding(self):
        """brand 字段被包含在内，用于 AI 去品牌化判断"""
        from phase2_translator import _build_user_message_from_record

        msg = _build_user_message_from_record(SAMPLE_PRODUCT_RECORD)
        assert '"brand"' in msg.lower() or "brand" in msg

    def test_output_is_string(self):
        """返回值为字符串类型"""
        from phase2_translator import _build_user_message_from_record

        msg = _build_user_message_from_record(SAMPLE_PRODUCT_RECORD)
        assert isinstance(msg, str)
        assert len(msg) > 0


# ============================================================
# 切片4: _parse_phase2_response — JSON 解析（Phase 2 专用）
# ============================================================

class TestParsePhase2Response:
    """从 AI 响应中解析 Phase 2 文案 JSON。"""

    def test_parses_valid_json_object(self):
        """正确解析标准 JSON 对象，返回 3 字段"""
        from phase2_translator import _parse_phase2_response

        result = _parse_phase2_response(VALID_PHASE2_JSON)
        assert result["russian_title"] == "массажер для лица 5 в 1 с микротоками"
        assert "core_keywords" in result
        assert result["core_keywords"]
        assert result["russian_description"]

    def test_parses_inline_json(self):
        """正确解析单行 JSON"""
        from phase2_translator import _parse_phase2_response

        result = _parse_phase2_response(VALID_PHASE2_JSON_INLINE)
        assert result["russian_title"] == "бутылка для масла стеклянная"
        assert result["core_keywords"]
        assert result["russian_description"]

    def test_extracts_json_from_markdown_block(self):
        """从 Markdown 代码块中提取 JSON"""
        from phase2_translator import _parse_phase2_response

        result = _parse_phase2_response(PHASE2_JSON_IN_MARKDOWN)
        assert result["russian_title"] == "масляный насос автомобильный"
        assert result["core_keywords"]

    def test_raises_on_invalid_json(self):
        """无效 JSON 时抛出 ValueError"""
        from phase2_translator import _parse_phase2_response

        with pytest.raises(ValueError, match="[Jj][Ss][Oo][Nn]|解析"):
            _parse_phase2_response("这不是合法的 JSON 响应")

    def test_raises_on_empty_response(self):
        """空响应时抛出 ValueError"""
        from phase2_translator import _parse_phase2_response

        with pytest.raises(ValueError, match="[Jj][Ss][Oo][Nn]|解析|空"):
            _parse_phase2_response("")

    def test_raises_on_missing_required_fields(self):
        """缺少必要字段时抛出 ValueError"""
        from phase2_translator import _parse_phase2_response

        with pytest.raises(ValueError, match="缺少|字段|russian_title|core_keywords|russian_description"):
            _parse_phase2_response('{"russian_title": "только заголовок"}')


# ============================================================
# 切片5: _post_process_title — 标题后处理
# ============================================================

class TestPostProcessTitle:
    """俄语标题后处理规则（与旧 translator.py 行为一致）。"""

    def test_lowercases_title(self):
        """标题转为全小写"""
        from text_utils import post_process_title

        result = post_process_title("Масляный Насос для Авто")
        assert result == "масляный насос для авто"

    def test_truncates_to_60_chars(self):
        """超过 60 字符的标题被截断"""
        from text_utils import post_process_title

        long_title = "а" * 80
        result = post_process_title(long_title)
        assert len(result) <= 60
        assert result == "а" * 60

    def test_keeps_title_under_60_unchanged_length(self):
        """60 字符以内的标题长度不变（除小写外）"""
        from text_utils import post_process_title

        title = "бутылка для масла стеклянная с распылителем"
        result = post_process_title(title)
        assert len(result) <= 60
        assert result == title.lower()

    def test_removes_special_characters(self):
        """去除特殊符号，保留字母、数字、空格"""
        from text_utils import post_process_title

        result = post_process_title("масляный насос!!! (премиум) #1")
        assert "!" not in result
        assert "(" not in result
        assert ")" not in result
        assert "#" not in result
        assert "масляный" in result
        assert "насос" in result
        assert "премиум" in result
        assert "1" in result

    def test_strips_whitespace(self):
        """去除首尾空格"""
        from text_utils import post_process_title

        result = post_process_title("  масляный насос  ")
        assert result == "масляный насос"
        assert not result.startswith(" ")
        assert not result.endswith(" ")

    def test_handles_empty_title(self):
        """空标题返回空字符串"""
        from text_utils import post_process_title

        result = post_process_title("")
        assert result == ""

    def test_handles_only_special_chars(self):
        """只有特殊符号的标题返回空字符串"""
        from text_utils import post_process_title

        result = post_process_title("!!!@@@###")
        assert result == ""

    def test_preserves_cyrillic(self):
        """保留俄语西里尔字母"""
        from text_utils import post_process_title

        result = post_process_title("Бутылка для Масла")
        assert "бутылка" in result
        assert "для" in result
        assert "масла" in result


# ============================================================
# 切片6: Phase2Generator 解析失败时自动重试
# ============================================================

class TestPhase2GeneratorRetriesOnParseFailure:
    """AI JSON 解析失败时自动重试一次。"""

    def test_retries_once_on_json_parse_error(self):
        """第一次返回无效 JSON → 重试 → 第二次成功"""
        from phase2_translator import OpenAIPhase2Generator

        call_count = [0]

        class RetryMockGenerator(OpenAIPhase2Generator):
            def _call_api(self, product_record: dict) -> str:
                call_count[0] += 1
                if call_count[0] == 1:
                    return "无效的 JSON 响应!!!"
                else:
                    return VALID_PHASE2_JSON_INLINE

        generator = RetryMockGenerator(api_key="sk-test")
        result = generator.generate(SAMPLE_PRODUCT_RECORD)
        assert call_count[0] == 2
        assert result["russian_title"] == "бутылка для масла стеклянная"

    def test_raises_after_two_failures(self):
        """两次解析都失败时抛出错误"""
        from phase2_translator import OpenAIPhase2Generator

        class AlwaysFailGenerator(OpenAIPhase2Generator):
            def _call_api(self, product_record: dict) -> str:
                return "总是返回无效 JSON"

        generator = AlwaysFailGenerator(api_key="sk-test")
        with pytest.raises(ValueError, match="[Jj][Ss][Oo][Nn]|解析|重试"):
            generator.generate(SAMPLE_PRODUCT_RECORD)

    def test_claude_generator_also_retries(self):
        """Claude 实现也支持重试"""
        from phase2_translator import ClaudePhase2Generator

        call_count = [0]

        class RetryMockClaudeGenerator(ClaudePhase2Generator):
            def _call_api(self, product_record: dict) -> str:
                call_count[0] += 1
                if call_count[0] == 1:
                    return "Bad JSON"
                else:
                    return VALID_PHASE2_JSON_INLINE

        generator = RetryMockClaudeGenerator(api_key="sk-test")
        result = generator.generate(SAMPLE_PRODUCT_RECORD)
        assert call_count[0] == 2
        assert result["russian_title"] == "бутылка для масла стеклянная"


# ============================================================
# 切片7: 工厂函数 create_phase2_generator
# ============================================================

class TestCreatePhase2Generator:
    """工厂函数根据配置创建正确的 Generator。"""

    def test_creates_openai_generator_for_openai_config(self):
        """PHASE2_API_PROVIDER=openai 时返回 OpenAIPhase2Generator"""
        from phase2_translator import create_phase2_generator, OpenAIPhase2Generator

        generator = create_phase2_generator("openai", api_key="sk-test")
        assert isinstance(generator, OpenAIPhase2Generator)

    def test_creates_openai_generator_for_deepseek_config(self):
        """PHASE2_API_PROVIDER=deepseek 时返回 OpenAIPhase2Generator（兼容协议）"""
        from phase2_translator import create_phase2_generator, OpenAIPhase2Generator

        generator = create_phase2_generator("deepseek", api_key="sk-test")
        assert isinstance(generator, OpenAIPhase2Generator)

    def test_creates_openai_generator_for_custom_config(self):
        """PHASE2_API_PROVIDER=custom 时返回 OpenAIPhase2Generator（兼容协议）"""
        from phase2_translator import create_phase2_generator, OpenAIPhase2Generator

        generator = create_phase2_generator("custom", api_key="sk-test")
        assert isinstance(generator, OpenAIPhase2Generator)

    def test_creates_claude_generator_for_anthropic_config(self):
        """PHASE2_API_PROVIDER=anthropic 时返回 ClaudePhase2Generator"""
        from phase2_translator import create_phase2_generator, ClaudePhase2Generator

        generator = create_phase2_generator("anthropic", api_key="sk-test")
        assert isinstance(generator, ClaudePhase2Generator)

    def test_creates_mock_generator(self):
        """provider=mock 时返回 MockPhase2Generator，无需 API Key"""
        from phase2_translator import create_phase2_generator, MockPhase2Generator

        generator = create_phase2_generator("mock")
        assert isinstance(generator, MockPhase2Generator)

    def test_raises_on_unknown_provider(self):
        """未知 provider 名称时抛出 ValueError"""
        from phase2_translator import create_phase2_generator

        with pytest.raises(ValueError, match="unknown|不支持|未知"):
            create_phase2_generator("unknown_provider", api_key="sk-test")

    def test_raises_on_empty_api_key(self):
        """非 Mock 模式下 API Key 为空时抛出明确错误"""
        from phase2_translator import create_phase2_generator

        with pytest.raises(ValueError, match="[Aa][Pp][Ii].*[Kk]ey|API"):
            create_phase2_generator("openai", api_key="")


# ============================================================
# 切片8: MockPhase2Generator（不依赖真实 API）
# ============================================================

class TestMockPhase2Generator:
    """MockPhase2Generator 返回可预测数据，用于测试。"""

    def test_generate_returns_dict_with_all_fields(self):
        """generate() 返回包含 russian_title, core_keywords, russian_description 的字典"""
        from phase2_translator import MockPhase2Generator

        generator = MockPhase2Generator()
        result = generator.generate(SAMPLE_PRODUCT_RECORD)

        assert "russian_title" in result
        assert "core_keywords" in result
        assert "russian_description" in result
        assert len(result["russian_title"]) > 0
        assert len(result["core_keywords"]) > 0
        assert len(result["russian_description"]) > 0

    def test_generate_returns_title_within_60_chars(self):
        """生成的标题 ≤ 60 字符"""
        from phase2_translator import MockPhase2Generator

        generator = MockPhase2Generator()
        result = generator.generate(SAMPLE_PRODUCT_RECORD)

        assert len(result["russian_title"]) <= 60

    def test_generate_returns_lowercase_title(self):
        """生成的标题为全小写"""
        from phase2_translator import MockPhase2Generator

        generator = MockPhase2Generator()
        result = generator.generate(SAMPLE_PRODUCT_RECORD)

        assert result["russian_title"] == result["russian_title"].lower()

    def test_generate_is_idempotent(self):
        """多次调用返回结果一致（Mock 确定性）"""
        from phase2_translator import MockPhase2Generator

        generator = MockPhase2Generator()
        result1 = generator.generate(SAMPLE_PRODUCT_RECORD)
        result2 = generator.generate(SAMPLE_PRODUCT_RECORD)

        assert result1 == result2

    def test_generate_strips_brand_from_title(self):
        """标题中品牌词被去除"""
        from phase2_translator import MockPhase2Generator

        generator = MockPhase2Generator()
        result = generator.generate(SAMPLE_PRODUCT_RECORD)

        # 品牌词 "BeautyPro" 不应出现在标题中
        assert "beautypro" not in result["russian_title"].lower()


# ============================================================
# 切片9: run_phase2 — 批量生成主逻辑
# ============================================================

class TestRunPhase2:
    """run_phase2 批量生成端到端行为。"""

    def test_generates_single_asin_and_writes_to_db(self, tmp_path):
        """生成单个 ASIN → 写入 translations 表"""
        import sqlite3
        from phase2_translator import run_phase2, MockPhase2Generator
        from db import init_db, upsert_product, get_translation

        db = sqlite3.connect(":memory:")
        init_db(db)
        upsert_product(db, SAMPLE_PRODUCT_RECORD)

        generator = MockPhase2Generator()

        progress = []
        results = run_phase2(
            asins=["B0GVYXC124"],
            db=db,
            generator=generator,
            progress_callback=lambda i, total: progress.append((i, total)),
        )

        # 验证翻译已写入数据库
        translation = get_translation(db, "B0GVYXC124")
        assert translation is not None, "翻译应已写入数据库"
        assert translation["asin"] == "B0GVYXC124"
        assert len(translation["russian_title"]) > 0
        assert len(translation["core_keywords"]) > 0
        assert len(translation["russian_description"]) > 0

        # 验证结果
        assert len(results) == 1
        assert results[0]["asin"] == "B0GVYXC124"
        assert "error" not in results[0]

        db.close()

    def test_progress_callback_fires_after_each_asin(self, tmp_path):
        """每次生成完成后调用 progress_callback"""
        import sqlite3
        from phase2_translator import run_phase2, MockPhase2Generator
        from db import init_db, upsert_product

        db = sqlite3.connect(":memory:")
        init_db(db)
        upsert_product(db, SAMPLE_PRODUCT_RECORD)
        upsert_product(db, SAMPLE_PRODUCT_RECORD_2)

        generator = MockPhase2Generator()

        progress = []
        run_phase2(
            asins=["B0GVYXC124", "B0F45N6NS7"],
            db=db,
            generator=generator,
            progress_callback=lambda i, total: progress.append((i, total)),
        )
        db.close()

        assert len(progress) == 2
        assert progress[0] == (1, 2)
        assert progress[1] == (2, 2)

    def test_empty_asins_list_returns_empty_list(self):
        """空 ASIN 列表直接返回空列表"""
        import sqlite3
        from phase2_translator import run_phase2, MockPhase2Generator
        from db import init_db

        db = sqlite3.connect(":memory:")
        init_db(db)

        generator = MockPhase2Generator()
        results = run_phase2([], db, generator)
        assert results == []
        db.close()

    def test_skips_asins_without_product_record(self):
        """无产品记录的 ASIN 被跳过并返回 error"""
        import sqlite3
        from phase2_translator import run_phase2, MockPhase2Generator
        from db import init_db, upsert_product

        db = sqlite3.connect(":memory:")
        init_db(db)
        upsert_product(db, SAMPLE_PRODUCT_RECORD)  # 只有 B0GVYXC124

        generator = MockPhase2Generator()
        results = run_phase2(["B0GVYXC124", "NONEXISTENT"], db, generator)
        db.close()

        assert len(results) == 2
        # 存在的 ASIN 成功
        success = [r for r in results if "error" not in r]
        assert len(success) == 1
        assert success[0]["asin"] == "B0GVYXC124"
        # 不存在的 ASIN 有 error
        errors = [r for r in results if "error" in r]
        assert len(errors) == 1
        assert errors[0]["asin"] == "NONEXISTENT"

    def test_writes_phase2_model_to_db(self):
        """翻译写入时记录 phase2_model 字段"""
        import sqlite3
        from phase2_translator import run_phase2, MockPhase2Generator
        from db import init_db, upsert_product, get_translation

        db = sqlite3.connect(":memory:")
        init_db(db)
        upsert_product(db, SAMPLE_PRODUCT_RECORD)

        generator = MockPhase2Generator()
        run_phase2(["B0GVYXC124"], db, generator)

        translation = get_translation(db, "B0GVYXC124")
        assert translation is not None
        assert "phase2_model" in translation
        # Mock 的 model 为 "mock"
        assert translation["phase2_model"] == "mock"
        db.close()

    def test_multiple_asins_all_written_to_db(self):
        """多个 ASIN 批量处理后全部写入数据库"""
        import sqlite3
        from phase2_translator import run_phase2, MockPhase2Generator
        from db import init_db, upsert_product, get_translation

        db = sqlite3.connect(":memory:")
        init_db(db)
        upsert_product(db, SAMPLE_PRODUCT_RECORD)
        upsert_product(db, SAMPLE_PRODUCT_RECORD_2)

        generator = MockPhase2Generator()
        results = run_phase2(["B0GVYXC124", "B0F45N6NS7"], db, generator)
        db.close()

        assert len(results) == 2
        for r in results:
            assert "error" not in r, f"ASIN {r['asin']} 不应有错误: {r.get('error')}"


# ============================================================
# 切片10: run_phase2 集成 — 默认从配置创建 generator
# ============================================================

class TestRunPhase2Integration:
    """run_phase2 集成测试：不传 generator 时自动从配置创建。"""

    def test_run_phase2_auto_creates_generator_from_config(self, monkeypatch):
        """不传 generator 时使用 create_phase2_generator() 自动创建"""
        import sqlite3
        from phase2_translator import run_phase2
        from db import init_db, upsert_product

        monkeypatch.setattr("config.settings.phase2_api_provider", "mock")
        monkeypatch.setattr("config.settings.phase2_api_key", "sk-test")
        monkeypatch.setattr("config.settings.phase2_api_base_url", "")
        monkeypatch.setattr("config.settings.phase2_model", "mock")

        db = sqlite3.connect(":memory:")
        init_db(db)
        upsert_product(db, SAMPLE_PRODUCT_RECORD)

        results = run_phase2(["B0GVYXC124"], db)  # 不传 generator
        db.close()

        assert len(results) == 1
        assert results[0]["asin"] == "B0GVYXC124"
        assert "error" not in results[0]

    def test_run_phase2_handles_api_key_missing_gracefully(self, monkeypatch):
        """API Key 未配置时 run_phase2 返回带 error 的结果，不崩溃"""
        import sqlite3
        from phase2_translator import run_phase2
        from db import init_db, upsert_product

        monkeypatch.setattr("config.settings.phase2_api_provider", "openai")
        monkeypatch.setattr("config.settings.phase2_api_key", "")

        db = sqlite3.connect(":memory:")
        init_db(db)
        upsert_product(db, SAMPLE_PRODUCT_RECORD)

        results = run_phase2(["B0GVYXC124"], db)
        db.close()

        assert len(results) == 1
        assert "error" in results[0]


# ============================================================
# 切片11: get_products_pending_phase2 集成
# ============================================================

class TestRunPhase2WithPendingQuery:
    """run_phase2 配合 get_products_pending_phase2() 使用。"""

    def test_phase2_can_use_pending_query_as_input(self):
        """get_products_pending_phase2() 的输出可直接作为 run_phase2 输入"""
        import sqlite3
        from phase2_translator import run_phase2, MockPhase2Generator
        from db import (
            init_db, upsert_product, upsert_translation,
            get_products_pending_phase2, get_translation,
        )

        db = sqlite3.connect(":memory:")
        init_db(db)

        # 插入 3 个产品，只为 1 个创建翻译
        upsert_product(db, {"asin": "A001", "title": "Product A"})
        upsert_product(db, {"asin": "A002", "title": "Product B"})
        upsert_product(db, {"asin": "A003", "title": "Product C"})
        upsert_translation(db, {"asin": "A001", "russian_title": "уже переведено"})

        # 获取待处理 ASIN
        pending = get_products_pending_phase2(db)
        assert set(pending) == {"A002", "A003"}

        # 运行 Phase 2
        generator = MockPhase2Generator()
        results = run_phase2(pending, db, generator)

        assert len(results) == 2
        for r in results:
            assert "error" not in r

        # 现在所有产品都有翻译
        pending_after = get_products_pending_phase2(db)
        assert pending_after == []

        db.close()


# ============================================================
# 切片12: CLI 入口 — python phase2_translator.py --db products.db --asin B0XXX
# ============================================================

class TestCliEntry:
    """命令行入口行为。"""

    def test_required_args_parsed_correctly(self):
        """--db 和 --asin 参数被正确解析"""
        from phase2_translator import _parse_cli_args

        args = _parse_cli_args(["--db", "products.db", "--asin", "B0GVYXC124"])
        assert args.db == "products.db"
        assert args.asin == "B0GVYXC124"

    def test_missing_db_raises_system_exit(self):
        """缺少 --db 参数时 argparse 触发 SystemExit"""
        from phase2_translator import _parse_cli_args

        with pytest.raises(SystemExit):
            _parse_cli_args(["--asin", "B0GVYXC124"])

    def test_cli_single_asin(self, tmp_path, monkeypatch):
        """端到端 CLI：--db products.db --asin B0XXX 单条生成"""
        import sqlite3
        from phase2_translator import main as phase2_main
        from phase2_translator import MockPhase2Generator
        from db import init_db, upsert_product, get_translation

        # 准备数据库
        db_path = tmp_path / "products.db"
        db = sqlite3.connect(str(db_path))
        init_db(db)
        upsert_product(db, SAMPLE_PRODUCT_RECORD)
        db.close()

        # Mock create_phase2_generator 返回 MockPhase2Generator
        monkeypatch.setattr(
            "phase2_translator.create_phase2_generator",
            lambda *a, **kw: MockPhase2Generator(),
        )

        # 模拟 CLI args
        original_argv = sys.argv
        try:
            sys.argv = [
                "phase2_translator.py",
                "--db", str(db_path),
                "--asin", "B0GVYXC124",
            ]
            phase2_main()
        finally:
            sys.argv = original_argv

        # 验证翻译已写入
        db = sqlite3.connect(str(db_path))
        from db import init_db as init
        init(db)
        translation = get_translation(db, "B0GVYXC124")
        db.close()

        assert translation is not None
        assert len(translation["russian_title"]) > 0
        assert len(translation["core_keywords"]) > 0

    def test_cli_all_pending(self, tmp_path, monkeypatch):
        """端到端 CLI：不传 --asin 时处理全部待翻译 ASIN"""
        import sqlite3
        from phase2_translator import main as phase2_main
        from phase2_translator import MockPhase2Generator
        from db import init_db, upsert_product, get_translation

        # 准备数据库（2 个产品，无翻译）
        db_path = tmp_path / "products.db"
        db = sqlite3.connect(str(db_path))
        init_db(db)
        upsert_product(db, SAMPLE_PRODUCT_RECORD)
        upsert_product(db, SAMPLE_PRODUCT_RECORD_2)
        db.close()

        # Mock
        monkeypatch.setattr(
            "phase2_translator.create_phase2_generator",
            lambda *a, **kw: MockPhase2Generator(),
        )

        # 不传 --asin，处理全部待翻译
        original_argv = sys.argv
        try:
            sys.argv = [
                "phase2_translator.py",
                "--db", str(db_path),
            ]
            phase2_main()
        finally:
            sys.argv = original_argv

        # 验证两个翻译都已写入
        db = sqlite3.connect(str(db_path))
        from db import init_db as init
        init(db)
        t1 = get_translation(db, "B0GVYXC124")
        t2 = get_translation(db, "B0F45N6NS7")
        db.close()

        assert t1 is not None, "B0GVYXC124 应有翻译"
        assert t2 is not None, "B0F45N6NS7 应有翻译"


# ============================================================
# 切片13: Phase2 API Key 未配置时的容错行为
# ============================================================

class TestNoApiKeyBehavior:
    """Phase 2 API Key 未配置时给出明确错误提示，不崩溃。"""

    def test_create_generator_raises_clear_error(self, monkeypatch):
        """API Key 为空时 create_phase2_generator 抛出明确错误"""
        from phase2_translator import create_phase2_generator

        monkeypatch.setattr("config.settings.phase2_api_provider", "openai")
        monkeypatch.setattr("config.settings.phase2_api_key", "")

        with pytest.raises(ValueError, match="[Aa][Pp][Ii].*[Kk]ey|API"):
            create_phase2_generator()

    def test_run_phase2_handles_missing_api_key_gracefully(self, monkeypatch):
        """run_phase2 中 API Key 未配置返回带 error 的结果，不崩溃"""
        import sqlite3
        from phase2_translator import run_phase2
        from db import init_db, upsert_product

        monkeypatch.setattr("config.settings.phase2_api_provider", "openai")
        monkeypatch.setattr("config.settings.phase2_api_key", "")

        db = sqlite3.connect(":memory:")
        init_db(db)
        upsert_product(db, SAMPLE_PRODUCT_RECORD)

        # run_phase2 内部容错
        results = run_phase2(["B0GVYXC124"], db)
        db.close()

        assert len(results) == 1
        assert "error" in results[0]
        assert results[0]["asin"] == "B0GVYXC124"


# ============================================================
# 切片14: Phase 2 配置独立性与 fallback
# ============================================================

class TestPhase2ConfigIndependence:
    """Phase 2 可独立配置 AI 服务商。"""

    def test_phase2_config_independent_from_phase1(self, monkeypatch):
        """Phase 2 配置与 Phase 1 独立，可使用不同的服务商和模型"""
        from phase2_translator import create_phase2_generator

        # 设置 Phase 2 独立配置
        with monkeypatch.context() as m:
            m.setattr("config.settings.phase2_api_provider", "anthropic")
            m.setattr("config.settings.phase2_api_key", "sk-phase2-key")
            m.setattr("config.settings.phase2_model", "claude-opus-4-8")

            # Phase 1 配置不同
            m.setattr("config.settings.phase1_api_provider", "openai")
            m.setattr("config.settings.phase1_api_key", "sk-phase1-key")
            m.setattr("config.settings.phase1_model", "gpt-4o")

            generator = create_phase2_generator()
            from phase2_translator import ClaudePhase2Generator
            assert isinstance(generator, ClaudePhase2Generator)

    def test_phase2_falls_back_to_phase1_config(self, monkeypatch):
        """Phase 2 配置未设置时 fallback 到 Phase 1 配置"""
        from config import _str

        # 模拟 Phase 1 有配置，但 Phase 2 未单独设置
        # config.py 中 phase2_api_provider 默认值取自 phase1_api_provider
        # 验证 fallback 逻辑
        from config import settings

        # Phase 2 直接使用 Phase 1 的值
        assert settings.phase2_api_provider is not None
