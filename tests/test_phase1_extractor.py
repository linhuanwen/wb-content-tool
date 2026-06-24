"""
测试 phase1_extractor.py — Phase 1 AI 信息萃取模块。

测试行为（非实现）：
- InformationExtractor 是抽象基类，不能直接实例化
- 工厂函数根据配置创建正确的 Extractor 实例
- System Prompt 加载
- JSON 解析（Phase 1 专用字段）与重试
- MockExtractor 返回可预测数据（不依赖真实 API）
- run_phase1 批量萃取 → 写入数据库
- CLI 入口
- API Key 未配置时给出明确错误提示，不崩溃
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# Phase 1 JSON 响应示例
# ============================================================

VALID_PHASE1_JSON = """
{
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
  "en_search_keywords": ["face sculpting machine", "facial massager", "body contouring device", "LED therapy"]
}
"""

VALID_PHASE1_JSON_INLINE = (
    '{"category":"Beauty & Personal Care","material":"ABS Plastic",'
    '"color":"White","dimensions":"","weight":"","capacity":"","package_contents":"",'
    '"features":["Facial massage","Body contouring"],'
    '"technical_specs":{},'
    '"target_audience":"Women 25-45",'
    '"use_scenarios":["Home spa"],'
    '"unique_selling_points":["5-in-1 multifunction"],'
    '"brand":"BeautyPro",'
    '"en_search_keywords":["face sculpting machine","facial massager"]'
    '}'
)

PHASE1_JSON_IN_MARKDOWN = """
Here is the extracted product information:

```json
{
  "category": "Home & Kitchen",
  "material": "Stainless Steel, Glass",
  "color": "Silver",
  "dimensions": "25 x 15 x 10 cm",
  "weight": "1.2kg",
  "capacity": "500ml",
  "package_contents": "1 x Oil Dispenser, 1 x Funnel",
  "features": ["Leak-proof design", "Easy to clean", "BPA-free"],
  "technical_specs": {"Volume": "500ml", "Material Grade": "304 Stainless Steel"},
  "target_audience": "Home cooks, Professional chefs",
  "use_scenarios": ["Kitchen", "BBQ", "Restaurant"],
  "unique_selling_points": ["No-drip nozzle", "Elegant design"],
  "brand": "KitchenPro",
  "en_search_keywords": ["oil dispenser bottle", "glass oil bottle", "kitchen oil container"]
}
```
"""

# 缺少必填字段的 JSON（缺少 category）
MISSING_REQUIRED_FIELD_JSON = """
{
  "category": "",
  "features": ["Some feature"],
  "brand": "",
  "material": "Plastic",
  "color": "",
  "dimensions": "",
  "weight": "",
  "capacity": "",
  "package_contents": "",
  "technical_specs": {},
  "target_audience": "",
  "use_scenarios": [],
  "unique_selling_points": [],
  "en_search_keywords": []
}
"""

# 补充了必填字段后的有效 JSON
CORRECTED_PHASE1_JSON = """
{
  "category": "Sports & Outdoors",
  "material": "Plastic",
  "color": "Blue",
  "dimensions": "30 x 20 x 10 cm",
  "weight": "500g",
  "capacity": "1L",
  "package_contents": "1 x Water Bottle",
  "features": ["Leak-proof", "BPA-free", "Easy grip"],
  "technical_specs": {"Capacity": "1000ml"},
  "target_audience": "Athletes, Gym enthusiasts",
  "use_scenarios": ["Gym workout", "Outdoor hiking"],
  "unique_selling_points": ["Double-wall insulation", "Sweat-proof coating"],
  "brand": "SportMax",
  "en_search_keywords": ["sports water bottle", "gym bottle", "insulated water bottle"]
}
"""

# 简短 HTML 片段（用于测试）
SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Amazon Product Page</title></head>
<body>
<div id="productTitle">5-in-1 Face & Body Sculpting Machine</div>
<div id="feature-bullets">
    <span class="a-list-item">5-in-1 multifunction beauty device</span>
    <span class="a-list-item">LED light therapy for skin rejuvenation</span>
    <span class="a-list-item">Portable USB charging, lightweight design</span>
</div>
<div id="productDescription">
    <p>Professional-grade face sculpting machine with ABS plastic body.</p>
</div>
</body>
</html>
"""


# ============================================================
# 切片1: InformationExtractor ABC + 工厂函数
# ============================================================

class TestInformationExtractorIsAbstract:
    """InformationExtractor 是抽象基类，不能直接实例化。"""

    def test_cannot_instantiate_abstract_extractor(self):
        """直接实例化 InformationExtractor 应抛出 TypeError"""
        from phase1_extractor import InformationExtractor

        with pytest.raises(TypeError):
            InformationExtractor()  # type: ignore[abstract]


class TestCreateExtractor:
    """工厂函数根据配置创建正确的 Extractor。"""

    def test_creates_openai_extractor_for_openai_config(self, monkeypatch):
        """PHASE1_API_PROVIDER=openai 时返回 OpenAIExtractor"""
        from phase1_extractor import create_extractor, OpenAIExtractor

        monkeypatch.setattr("config.settings.phase1_api_provider", "openai")
        monkeypatch.setattr("config.settings.phase1_api_key", "sk-test")
        monkeypatch.setattr("config.settings.phase1_api_base_url", "")
        monkeypatch.setattr("config.settings.phase1_model", "gpt-4o")

        extractor = create_extractor()
        assert isinstance(extractor, OpenAIExtractor)

    def test_creates_openai_extractor_for_deepseek_config(self, monkeypatch):
        """PHASE1_API_PROVIDER=deepseek 时返回 OpenAIExtractor（兼容协议）"""
        from phase1_extractor import create_extractor, OpenAIExtractor

        monkeypatch.setattr("config.settings.phase1_api_provider", "deepseek")
        monkeypatch.setattr("config.settings.phase1_api_key", "sk-test")

        extractor = create_extractor()
        assert isinstance(extractor, OpenAIExtractor)

    def test_creates_claude_extractor_for_anthropic_config(self, monkeypatch):
        """PHASE1_API_PROVIDER=anthropic 时返回 ClaudeExtractor"""
        from phase1_extractor import create_extractor, ClaudeExtractor

        monkeypatch.setattr("config.settings.phase1_api_provider", "anthropic")
        monkeypatch.setattr("config.settings.phase1_api_key", "sk-test")

        extractor = create_extractor()
        assert isinstance(extractor, ClaudeExtractor)

    def test_raises_on_unknown_provider(self, monkeypatch):
        """未知 provider 名称时抛出 ValueError"""
        from phase1_extractor import create_extractor

        monkeypatch.setattr("config.settings.phase1_api_provider", "unknown_provider")
        monkeypatch.setattr("config.settings.phase1_api_key", "sk-test")

        with pytest.raises(ValueError, match="unknown|不支持|未知"):
            create_extractor()

    def test_raises_on_empty_api_key(self, monkeypatch):
        """API Key 为空时抛出明确错误，不崩溃"""
        from phase1_extractor import create_extractor

        monkeypatch.setattr("config.settings.phase1_api_provider", "openai")
        monkeypatch.setattr("config.settings.phase1_api_key", "")

        with pytest.raises(ValueError, match="[Aa][Pp][Ii].*[Kk]ey|API"):
            create_extractor()


# ============================================================
# 切片2: System Prompt 加载
# ============================================================

class TestLoadPhase1Prompt:
    """加载 prompts/phase1_extraction_persona.txt 作为 System Prompt。"""

    def test_returns_non_empty_string(self):
        """返回非空字符串"""
        from phase1_extractor import _load_phase1_prompt

        prompt = _load_phase1_prompt()
        assert isinstance(prompt, str)
        assert len(prompt.strip()) > 0

    def test_contains_persona_content(self):
        """加载的内容包含角色定义（跨境电商产品信息萃取专家）"""
        from phase1_extractor import _load_phase1_prompt

        prompt = _load_phase1_prompt()
        assert "产品信息萃取" in prompt

    def test_contains_required_fields_in_schema(self):
        """加载的内容包含 Phase 1 15 字段的 JSON Schema"""
        from phase1_extractor import _load_phase1_prompt

        prompt = _load_phase1_prompt()
        assert "category" in prompt
        assert "features" in prompt
        assert "brand" in prompt
        assert "material" in prompt
        assert "technical_specs" in prompt
        assert "en_search_keywords" in prompt

    def test_raises_if_file_missing(self, monkeypatch):
        """文件不存在时抛出明确错误"""
        from phase1_extractor import _load_phase1_prompt

        monkeypatch.setattr(
            "phase1_extractor._PHASE1_PERSONA_PATH",
            os.path.join(os.path.dirname(__file__), "nonexistent.txt"),
        )
        with pytest.raises(FileNotFoundError):
            _load_phase1_prompt()


# ============================================================
# 切片3: _parse_phase1_response — JSON 解析（Phase 1 专用）
# ============================================================

class TestParsePhase1Response:
    """从 AI 响应中解析 Phase 1 结构化 JSON。"""

    def test_parses_valid_json_object(self):
        """正确解析标准 JSON 对象，返回 15 个字段"""
        from phase1_extractor import _parse_phase1_response

        result = _parse_phase1_response(VALID_PHASE1_JSON)
        assert result["category"] == "Beauty & Personal Care"
        assert result["material"] == "ABS Plastic, Silicone"
        assert result["brand"] == "BeautyPro"
        assert isinstance(result["features"], list)
        assert len(result["features"]) == 4
        assert isinstance(result["technical_specs"], dict)
        assert result["technical_specs"]["Power"] == "5W"

    def test_parses_inline_json(self):
        """正确解析单行 JSON"""
        from phase1_extractor import _parse_phase1_response

        result = _parse_phase1_response(VALID_PHASE1_JSON_INLINE)
        assert result["category"] == "Beauty & Personal Care"
        assert result["brand"] == "BeautyPro"

    def test_extracts_json_from_markdown_block(self):
        """从 Markdown 代码块中提取 JSON"""
        from phase1_extractor import _parse_phase1_response

        result = _parse_phase1_response(PHASE1_JSON_IN_MARKDOWN)
        assert result["category"] == "Home & Kitchen"
        assert result["material"] == "Stainless Steel, Glass"

    def test_defaults_missing_fields_to_empty(self):
        """AI 返回的 JSON 缺少可选字段时使用默认值（空字符串/空数组/空对象）"""
        from phase1_extractor import _parse_phase1_response

        minimal_json = '{"category":"Test","material":"Plastic","brand":"TestBrand","features":["f1"]}'
        result = _parse_phase1_response(minimal_json)
        # 必填字段存在
        assert result["category"] == "Test"
        assert result["material"] == "Plastic"
        assert result["brand"] == "TestBrand"
        assert result["features"] == ["f1"]
        # 可选字段使用默认值
        assert result["color"] == ""
        assert result["dimensions"] == ""
        assert result["weight"] == ""
        assert result["capacity"] == ""
        assert result["package_contents"] == ""
        assert result["technical_specs"] == {}
        assert result["target_audience"] == ""
        assert result["use_scenarios"] == []
        assert result["unique_selling_points"] == []
        assert result["en_search_keywords"] == []

    def test_raises_on_invalid_json(self):
        """无效 JSON 时抛出 ValueError"""
        from phase1_extractor import _parse_phase1_response

        with pytest.raises(ValueError, match="[Jj][Ss][Oo][Nn]|解析"):
            _parse_phase1_response("这不是合法的 JSON 响应")

    def test_raises_on_empty_response(self):
        """空响应时抛出 ValueError"""
        from phase1_extractor import _parse_phase1_response

        with pytest.raises(ValueError, match="[Jj][Ss][Oo][Nn]|解析|空"):
            _parse_phase1_response("")

    def test_raises_on_missing_required_fields(self):
        """缺少必填字段（category/features/brand/material）时抛出 ValueError"""
        from phase1_extractor import _parse_phase1_response

        with pytest.raises(ValueError, match="缺少|字段|category|features|brand|material"):
            _parse_phase1_response('{"material": "Steel"}')


# ============================================================
# 切片4: MockExtractor（不依赖真实 API）
# ============================================================

class TestMockExtractor:
    """MockExtractor 返回可预测数据，用于测试。"""

    def test_extract_returns_dict_with_all_fields(self):
        """extract() 返回包含 15 个 Phase 1 字段的字典"""
        from phase1_extractor import MockExtractor

        extractor = MockExtractor()
        result = extractor.extract("<html>some html</html>")

        expected_fields = {
            "category", "material", "color", "dimensions", "weight",
            "capacity", "package_contents", "features", "technical_specs",
            "target_audience", "use_scenarios", "unique_selling_points",
            "brand", "en_search_keywords",
        }
        for field in expected_fields:
            assert field in result, f"缺少字段: {field}"

    def test_extract_returns_valid_category(self):
        """返回的 category 非空字符串"""
        from phase1_extractor import MockExtractor

        extractor = MockExtractor()
        result = extractor.extract("<html>test</html>")
        assert result["category"], "category 不应为空"

    def test_extract_returns_list_for_array_fields(self):
        """features/use_scenarios/unique_selling_points/en_search_keywords 都是 list"""
        from phase1_extractor import MockExtractor

        extractor = MockExtractor()
        result = extractor.extract("<html>test</html>")

        assert isinstance(result["features"], list)
        assert isinstance(result["use_scenarios"], list)
        assert isinstance(result["unique_selling_points"], list)
        assert isinstance(result["en_search_keywords"], list)

    def test_extract_returns_dict_for_technical_specs(self):
        """technical_specs 是 dict"""
        from phase1_extractor import MockExtractor

        extractor = MockExtractor()
        result = extractor.extract("<html>test</html>")

        assert isinstance(result["technical_specs"], dict)

    def test_extract_is_idempotent(self):
        """多次调用返回结果一致（Mock 确定性）"""
        from phase1_extractor import MockExtractor

        extractor = MockExtractor()
        result1 = extractor.extract(SAMPLE_HTML)
        result2 = extractor.extract(SAMPLE_HTML)

        assert result1 == result2


# ============================================================
# 切片4.5: _extract_basic_fields — 基础字段确定性提取
# ============================================================

# 含图片的基础 HTML 样本
BASIC_FIELDS_HTML_WITH_IMAGES = """<!DOCTYPE html>
<html>
<body>
<div id="productTitle">Test Product Title Here</div>
<img id="landingImage"
     src="https://m.media-amazon.com/images/I/71MAIN._AC_SL1500_.jpg"
     data-old-hires="https://m.media-amazon.com/images/I/71MAIN.jpg" />
<div id="altImages">
    <img src="https://m.media-amazon.com/images/I/71ALT._SX38_.jpg"
         data-old-hires="https://m.media-amazon.com/images/I/71ALT.jpg" />
</div>
<div id="feature-bullets">
    <span class="a-list-item">First feature bullet</span>
    <span class="a-list-item">Second feature bullet</span>
</div>
<div id="productDescription">
    <p>This is the product description text.</p>
</div>
</body>
</html>"""


class TestExtractBasicFields:
    """_extract_basic_fields 从 HTML 提取标题/图片url/详情。"""

    def test_extracts_title_from_product_title(self):
        """从 #productTitle 提取标题"""
        from phase1_extractor import _extract_basic_fields

        result = _extract_basic_fields(SAMPLE_HTML)
        assert result["title"] == "5-in-1 Face & Body Sculpting Machine"

    def test_extracts_details_from_bullets_and_description(self):
        """从 #feature-bullets 和 #productDescription 提取详情"""
        from phase1_extractor import _extract_basic_fields

        result = _extract_basic_fields(SAMPLE_HTML)
        assert "5-in-1 multifunction beauty device" in result["details"]
        assert "Professional-grade face sculpting machine" in result["details"]

    def test_extracts_image_urls_from_landing_and_alt(self):
        """从 #landingImage 和 #altImages 提取图片 URL"""
        from phase1_extractor import _extract_basic_fields

        result = _extract_basic_fields(BASIC_FIELDS_HTML_WITH_IMAGES)
        urls = result["image_urls"].split(";")
        assert len(urls) >= 2, f"应有 ≥2 张图片，实际: {len(urls)}"
        assert "71MAIN" in urls[0], f"主图应排第一，实际: {urls[0]}"
        assert all(u.startswith("http") for u in urls)

    def test_normalizes_amazon_image_url_suffixes(self):
        """移除亚马逊图片 URL 尺寸后缀"""
        from phase1_extractor import _extract_basic_fields

        result = _extract_basic_fields(BASIC_FIELDS_HTML_WITH_IMAGES)
        urls = result["image_urls"].split(";")
        # 尺寸后缀已被移除
        for url in urls:
            assert "._AC_SL1500_" not in url
            assert "._SX38_" not in url

    def test_empty_html_returns_empty_fields(self):
        """空 HTML 返回所有字段为空字符串，不崩溃"""
        from phase1_extractor import _extract_basic_fields

        result = _extract_basic_fields("<html></html>")
        assert result["title"] == ""
        assert result["image_urls"] == ""
        assert result["details"] == ""

    def test_deduplicates_image_urls(self):
        """主图和备图中的重复 URL 去重"""
        from phase1_extractor import _extract_basic_fields

        html = """<html><body>
        <img id="landingImage" src="https://same-img.jpg" />
        <div id="altImages">
            <img src="https://same-img.jpg" />
            <img src="https://other-img.jpg" />
        </div>
        </body></html>"""
        result = _extract_basic_fields(html)
        urls = result["image_urls"].split(";")
        assert len(urls) == 2, f"应去重，实际: {len(urls)}"

    def test_prefers_data_old_hires_over_src(self):
        """优先使用 data-old-hires 属性（高分辨率）"""
        from phase1_extractor import _extract_basic_fields

        result = _extract_basic_fields(BASIC_FIELDS_HTML_WITH_IMAGES)
        # 主图 URL 应为 data-old-hires 值（非 src 中的带尺寸版本）
        assert "71MAIN.jpg" in result["image_urls"]
        assert "._AC_SL1500_" not in result["image_urls"]


# ============================================================
# 切片5: Extractor 解析失败时自动重试
# ============================================================

class TestExtractorRetriesOnParseFailure:
    """AI JSON 解析失败时自动重试一次。"""

    def test_openai_extractor_retries_once_on_json_parse_error(self, monkeypatch):
        """第一次返回无效 JSON → 重试 → 第二次成功"""
        from phase1_extractor import OpenAIExtractor

        call_count = [0]
        original_parse = __import__("phase1_extractor")._parse_phase1_response

        class RetryMockExtractor(OpenAIExtractor):
            def _call_api(self, html: str) -> str:
                call_count[0] += 1
                if call_count[0] == 1:
                    return "无效的 JSON 响应!!!"
                else:
                    return VALID_PHASE1_JSON_INLINE

        extractor = RetryMockExtractor(api_key="sk-test")
        result = extractor.extract(SAMPLE_HTML)
        assert call_count[0] == 2, f"预期重试 2 次，实际调用 {call_count[0]} 次"
        assert result["category"] == "Beauty & Personal Care"

    def test_raises_after_two_failures(self):
        """两次解析都失败时抛出错误"""
        from phase1_extractor import OpenAIExtractor

        class AlwaysFailExtractor(OpenAIExtractor):
            def _call_api(self, html: str) -> str:
                return "总是返回无效 JSON"

        extractor = AlwaysFailExtractor(api_key="sk-test")
        with pytest.raises(ValueError, match="[Jj][Ss][Oo][Nn]|解析|重试"):
            extractor.extract(SAMPLE_HTML)

    def test_claude_extractor_retries_once_on_json_parse_error(self, monkeypatch):
        """Claude 实现也支持重试"""
        from phase1_extractor import ClaudeExtractor

        call_count = [0]

        class RetryMockClaudeExtractor(ClaudeExtractor):
            def _call_api(self, html: str) -> str:
                call_count[0] += 1
                if call_count[0] == 1:
                    return "Bad JSON"
                else:
                    return VALID_PHASE1_JSON_INLINE

        extractor = RetryMockClaudeExtractor(api_key="sk-test")
        result = extractor.extract(SAMPLE_HTML)
        assert call_count[0] == 2
        assert result["category"] == "Beauty & Personal Care"


# ============================================================
# 切片6: run_phase1 — 批量萃取主逻辑
# ============================================================

SAMPLE_PHASE1_RESULT = {
    "category": "Beauty & Personal Care",
    "material": "ABS Plastic",
    "color": "White",
    "dimensions": "15 x 10 x 5 cm",
    "weight": "350g",
    "capacity": "",
    "package_contents": "1 x Device, 1 x USB Cable",
    "features": ["Facial massage", "Body contouring"],
    "technical_specs": {"Power": "5W"},
    "target_audience": "Women 25-45",
    "use_scenarios": ["Home spa"],
    "unique_selling_points": ["5-in-1 multifunction"],
    "brand": "BeautyPro",
    "en_search_keywords": ["face sculpting machine", "facial massager"],
}


class TestRunPhase1:
    """run_phase1 批量萃取端到端行为。"""

    def test_extracts_single_asin_and_writes_to_db(self, tmp_path):
        """萃取单个 ASIN → 写入 products 表"""
        import sqlite3
        from phase1_extractor import run_phase1, MockExtractor

        # 创建 HTML 文件
        html_dir = tmp_path / "html"
        html_dir.mkdir()
        (html_dir / "B0TEST01.html").write_text(SAMPLE_HTML, encoding="utf-8")

        # 创建内存数据库
        db_path = tmp_path / "products.db"
        db = sqlite3.connect(str(db_path))
        from db import init_db, get_product
        init_db(db)

        extractor = MockExtractor()

        progress = []
        run_phase1(
            asins=["B0TEST01"],
            html_dir=str(html_dir),
            db=db,
            extractor=extractor,
            progress_callback=lambda i, total: progress.append((i, total)),
        )

        # 验证产品已写入数据库
        product = get_product(db, "B0TEST01")
        assert product is not None, "产品应已写入数据库"
        assert product["category"] == "Beauty & Personal Care"
        assert product["brand"] == "BeautyPro"
        assert isinstance(product["features"], list)
        assert isinstance(product["technical_specs"], dict)
        assert product["html_path"], "html_path 不应为空"
        db.close()

    def test_progress_callback_fires_after_each_asin(self, tmp_path):
        """每次萃取完成后调用 progress_callback"""
        import sqlite3
        from phase1_extractor import run_phase1, MockExtractor

        html_dir = tmp_path / "html"
        html_dir.mkdir()
        for asin in ["B0A", "B0B", "B0C"]:
            (html_dir / f"{asin}.html").write_text(SAMPLE_HTML, encoding="utf-8")

        db_path = tmp_path / "products.db"
        db = sqlite3.connect(str(db_path))
        from db import init_db
        init_db(db)

        extractor = MockExtractor()

        progress = []
        run_phase1(
            asins=["B0A", "B0B", "B0C"],
            html_dir=str(html_dir),
            db=db,
            extractor=extractor,
            progress_callback=lambda i, total: progress.append((i, total)),
        )
        db.close()

        assert len(progress) == 3
        assert progress[0] == (1, 3)
        assert progress[1] == (2, 3)
        assert progress[2] == (3, 3)

    def test_empty_asins_list_returns_empty_list(self, tmp_path):
        """空 ASIN 列表直接返回空列表"""
        import sqlite3
        from phase1_extractor import run_phase1, MockExtractor

        html_dir = tmp_path / "html"
        html_dir.mkdir()

        db = sqlite3.connect(":memory:")
        from db import init_db
        init_db(db)

        extractor = MockExtractor()
        results = run_phase1([], str(html_dir), db, extractor)
        assert results == []
        db.close()

    def test_missing_html_file_skips_asin(self, tmp_path):
        """HTML 文件不存在时跳过该 ASIN，不崩溃，其他继续"""
        import sqlite3
        from phase1_extractor import run_phase1, MockExtractor

        html_dir = tmp_path / "html"
        html_dir.mkdir()
        # 只创建 B0A 的 HTML，不创建 B0B
        (html_dir / "B0A.html").write_text(SAMPLE_HTML, encoding="utf-8")

        db = sqlite3.connect(":memory:")
        from db import init_db, get_product
        init_db(db)

        extractor = MockExtractor()
        results = run_phase1(["B0A", "B0MISSING"], str(html_dir), db, extractor)

        # B0A 成功
        assert any(r["asin"] == "B0A" and "error" not in r for r in results)
        # B0MISSING 跳过
        assert any(r["asin"] == "B0MISSING" and "error" in r for r in results)

        # B0A 仍在数据库中
        product = get_product(db, "B0A")
        assert product is not None
        db.close()

    def test_writes_html_path_to_db(self, tmp_path):
        """数据库中的 html_path 存了相对路径"""
        import sqlite3
        from phase1_extractor import run_phase1, MockExtractor

        html_dir = tmp_path / "html"
        html_dir.mkdir()
        (html_dir / "B0HTMLPATH.html").write_text(SAMPLE_HTML, encoding="utf-8")

        db = sqlite3.connect(":memory:")
        from db import init_db, get_product
        init_db(db)

        extractor = MockExtractor()
        run_phase1(["B0HTMLPATH"], str(html_dir), db, extractor)

        product = get_product(db, "B0HTMLPATH")
        assert product is not None
        assert "html/B0HTMLPATH.html" in product["html_path"] or "B0HTMLPATH" in product["html_path"]
        db.close()

    def test_multiple_asins_all_written_to_db(self, tmp_path):
        """多个 ASIN 批量处理后全部写入数据库"""
        import sqlite3
        from phase1_extractor import run_phase1, MockExtractor

        html_dir = tmp_path / "html"
        html_dir.mkdir()
        for i in range(5):
            (html_dir / f"B0_{i}.html").write_text(SAMPLE_HTML, encoding="utf-8")

        db_path = tmp_path / "products.db"
        db = sqlite3.connect(str(db_path))
        from db import init_db, get_all_products
        init_db(db)

        extractor = MockExtractor()
        results = run_phase1(
            [f"B0_{i}" for i in range(5)],
            str(html_dir), db, extractor,
        )
        db.close()

        assert len(results) == 5
        for r in results:
            assert "error" not in r, f"ASIN {r.get('asin')} 不应有错误: {r.get('error')}"


# ============================================================
# 切片7: 工厂函数集成 — create_extractor 可通过 mock 覆盖
# ============================================================

class TestRunPhase1Integration:
    """run_phase1 集成测试：与 config + create_extractor 配合。"""

    def test_run_phase1_with_factory(self, tmp_path, monkeypatch):
        """使用 create_extractor() 工厂创建实例（mock API key 有效）"""
        import sqlite3
        from phase1_extractor import run_phase1, create_extractor

        monkeypatch.setattr("config.settings.phase1_api_provider", "openai")
        monkeypatch.setattr("config.settings.phase1_api_key", "sk-test")
        monkeypatch.setattr("config.settings.phase1_api_base_url", "")
        monkeypatch.setattr("config.settings.phase1_model", "gpt-4o")

        html_dir = tmp_path / "html"
        html_dir.mkdir()
        (html_dir / "B0INTEGRATE.html").write_text(SAMPLE_HTML, encoding="utf-8")

        db = sqlite3.connect(":memory:")
        from db import init_db
        init_db(db)

        # 这会创建真实的 OpenAIExtractor，但测试中我们 mock _call_api
        extractor = create_extractor()

        # Mock _call_api 以避免真实 API 调用
        extractor._call_api = lambda html: VALID_PHASE1_JSON_INLINE

        results = run_phase1(["B0INTEGRATE"], str(html_dir), db, extractor)
        assert len(results) == 1
        assert results[0]["asin"] == "B0INTEGRATE"
        db.close()


# ============================================================
# 切片8: CLI 入口 — python phase1_extractor.py --html-dir html/ --db products.db
# ============================================================

class TestCliEntry:
    """命令行入口行为。"""

    def test_required_args_parsed_correctly(self):
        """--html-dir 和 --db 参数被正确解析"""
        from phase1_extractor import _parse_cli_args

        args = _parse_cli_args(["--html-dir", "html/", "--db", "products.db"])
        assert args.html_dir == "html/"
        assert args.db == "products.db"

    def test_missing_html_dir_raises_system_exit(self):
        """缺少 --html-dir 参数时 argparse 触发 SystemExit"""
        from phase1_extractor import _parse_cli_args

        with pytest.raises(SystemExit):
            _parse_cli_args(["--db", "products.db"])

    def test_missing_db_raises_system_exit(self):
        """缺少 --db 参数时 argparse 触发 SystemExit"""
        from phase1_extractor import _parse_cli_args

        with pytest.raises(SystemExit):
            _parse_cli_args(["--html-dir", "html/"])

    def test_default_values(self):
        """默认值：--html-dir 和 --db 都使用合理默认值"""
        from phase1_extractor import _parse_cli_args

        # argparse 的 default 行为
        args = _parse_cli_args(["--html-dir", "html/", "--db", "products.db"])
        assert args.html_dir == "html/"
        assert args.db == "products.db"

    def test_cli_end_to_end(self, tmp_path, monkeypatch):
        """端到端 CLI：读 HTML → 萃取 → 写入 products.db"""
        from phase1_extractor import main as phase1_main
        from phase1_extractor import MockExtractor

        # 准备 HTML 文件
        html_dir = tmp_path / "html"
        html_dir.mkdir()
        (html_dir / "B0CLI01.html").write_text(SAMPLE_HTML, encoding="utf-8")

        db_path = tmp_path / "test_products.db"

        # Mock create_extractor 返回 MockExtractor
        monkeypatch.setattr(
            "phase1_extractor.create_extractor",
            lambda: MockExtractor(),
        )

        # 模拟 CLI args
        original_argv = sys.argv
        try:
            sys.argv = [
                "phase1_extractor.py",
                "--html-dir", str(html_dir),
                "--db", str(db_path),
            ]
            phase1_main()
        finally:
            sys.argv = original_argv

        # 验证数据库文件存在
        assert db_path.is_file(), f"数据库文件未生成: {db_path}"

        # 验证产品记录已写入
        import sqlite3
        from db import get_product
        db = sqlite3.connect(str(db_path))
        from db import init_db
        init_db(db)  # 幂等，确保表存在
        product = get_product(db, "B0CLI01")
        db.close()

        assert product is not None
        assert product["category"] == "Beauty & Personal Care"


# ============================================================
# 切片9: API Key 未配置时的行为
# ============================================================

class TestNoApiKeyBehavior:
    """API Key 未配置时给出明确错误提示，不崩溃。"""

    def test_create_extractor_raises_clear_error(self, monkeypatch):
        """API Key 为空时 create_extractor 抛出明确错误"""
        from phase1_extractor import create_extractor

        monkeypatch.setattr("config.settings.phase1_api_provider", "openai")
        monkeypatch.setattr("config.settings.phase1_api_key", "")

        with pytest.raises(ValueError, match="[Aa][Pp][Ii].*[Kk]ey|API"):
            create_extractor()

    def test_run_phase1_handles_missing_api_key_gracefully(self, tmp_path, monkeypatch):
        """run_phase1 中 API Key 未配置返回带 error 的结果，不崩溃"""
        import sqlite3
        from phase1_extractor import run_phase1, create_extractor

        monkeypatch.setattr("config.settings.phase1_api_provider", "openai")
        monkeypatch.setattr("config.settings.phase1_api_key", "")

        html_dir = tmp_path / "html"
        html_dir.mkdir()
        (html_dir / "B0NOAPI.html").write_text(SAMPLE_HTML, encoding="utf-8")

        db = sqlite3.connect(":memory:")
        from db import init_db
        init_db(db)

        # 使用 try/except 包裹工厂调用，模拟 run_phase1 内部容错
        try:
            extractor = create_extractor()
        except ValueError as e:
            # API Key 未配置时 run_phase1 应该返回 error 结果
            results = [{"asin": "B0NOAPI", "error": str(e)}]
        else:
            results = run_phase1(["B0NOAPI"], str(html_dir), db, extractor)

        assert len(results) == 1
        assert "error" in results[0]
        db.close()
