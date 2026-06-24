"""
测试 db.py — SQLite 产品信息数据库模块。

测试行为（非实现）：
- init_db() 创建 products 和 translations 表
- upsert_product() 插入/更新产品记录
- get_product() 读取产品记录
- upsert_translation() 插入/更新翻译记录
- get_translation() 读取翻译记录
- get_all_products() 列出全部产品
- get_products_pending_phase2() 返回有产品但无翻译的 ASIN
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 切片1: init_db() — 创建表结构
# ============================================================

class TestInitDb:
    """init_db() 创建 products 和 translations 两张表。"""

    def test_creates_products_table(self):
        """init_db() 后 products 表存在且包含所有必要列"""
        import sqlite3
        from db import init_db

        db = sqlite3.connect(":memory:")
        init_db(db)

        # 验证表存在
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='products'"
        )
        assert cursor.fetchone() is not None, "products 表未被创建"

        # 验证列
        cols = {row[1] for row in db.execute("PRAGMA table_info(products)")}
        required = {
            "asin", "title", "details", "image_urls", "html_path",
            "category", "material", "color", "dimensions", "weight",
            "capacity", "package_contents", "features", "technical_specs",
            "target_audience", "use_scenarios", "unique_selling_points",
            "brand", "en_search_keywords", "created_at",
        }
        missing = required - cols
        assert not missing, f"products 表缺少列: {missing}"

        db.close()

    def test_creates_translations_table(self):
        """init_db() 后 translations 表存在且包含所有必要列"""
        import sqlite3
        from db import init_db

        db = sqlite3.connect(":memory:")
        init_db(db)

        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='translations'"
        )
        assert cursor.fetchone() is not None, "translations 表未被创建"

        cols = {row[1] for row in db.execute("PRAGMA table_info(translations)")}
        required = {
            "asin", "russian_title", "core_keywords", "russian_description",
            "phase1_model", "phase2_model", "created_at",
        }
        missing = required - cols
        assert not missing, f"translations 表缺少列: {missing}"

        db.close()

    def test_asin_is_primary_key_in_products(self):
        """products 表的 asin 列是主键"""
        import sqlite3
        from db import init_db

        db = sqlite3.connect(":memory:")
        init_db(db)

        pks = {row[1] for row in db.execute("PRAGMA table_info(products)") if row[5]}
        assert "asin" in pks, "asin 应为 products 表主键"

        db.close()

    def test_asin_is_primary_key_in_translations(self):
        """translations 表的 asin 列是主键"""
        import sqlite3
        from db import init_db

        db = sqlite3.connect(":memory:")
        init_db(db)

        pks = {row[1] for row in db.execute("PRAGMA table_info(translations)") if row[5]}
        assert "asin" in pks, "asin 应为 translations 表主键"

        db.close()

    def test_init_db_is_idempotent(self):
        """多次调用 init_db() 不报错（CREATE TABLE IF NOT EXISTS）"""
        import sqlite3
        from db import init_db

        db = sqlite3.connect(":memory:")
        init_db(db)
        # 第二次调用不应抛出异常
        init_db(db)
        # 表仍然存在
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='products'"
        )
        assert cursor.fetchone() is not None
        db.close()


# ============================================================
# 切片2: upsert_product() + get_product() — 产品 CRUD
# ============================================================

SAMPLE_PRODUCT = {
    "asin": "B0GVYXC124",
    "title": "5-in-1 Face Sculpting Machine",
    "details": "Multifunctional body contouring machine.",
    "image_urls": "https://img1.jpg;https://img2.jpg",
    "html_path": "html/B0GVYXC124.html",
    "category": "Beauty & Personal Care",
    "material": "ABS Plastic",
    "color": "White",
    "dimensions": "15 x 10 x 5 cm",
    "weight": "350g",
    "capacity": "",
    "package_contents": "1 x Device, 1 x USB Cable",
    "features": ["Facial massage", "Body contouring", "5 modes"],
    "technical_specs": {"power": "5W", "battery": "2000mAh"},
    "target_audience": "Women 25-45",
    "use_scenarios": ["Home spa", "Daily skincare"],
    "unique_selling_points": ["5-in-1 multifunction", "Portable USB charging"],
    "brand": "BeautyPro",
    "en_search_keywords": ["face sculpting machine", "facial massager", "body contouring"],
}


class TestUpsertProduct:
    """upsert_product() 插入新产品记录。"""

    def test_inserts_new_product(self):
        """插入新产品后可通过 get_product 读取"""
        import sqlite3
        from db import init_db, upsert_product, get_product

        db = sqlite3.connect(":memory:")
        init_db(db)

        upsert_product(db, SAMPLE_PRODUCT)
        result = get_product(db, "B0GVYXC124")

        assert result is not None, "插入后应能读取到产品"
        assert result["asin"] == "B0GVYXC124"
        assert result["title"] == "5-in-1 Face Sculpting Machine"
        assert result["details"] == "Multifunctional body contouring machine."
        assert result["category"] == "Beauty & Personal Care"
        db.close()

    def test_inserts_all_text_fields(self):
        """所有文本字段正确保存和读取"""
        import sqlite3
        from db import init_db, upsert_product, get_product

        db = sqlite3.connect(":memory:")
        init_db(db)

        upsert_product(db, SAMPLE_PRODUCT)
        result = get_product(db, "B0GVYXC124")

        assert result["material"] == "ABS Plastic"
        assert result["color"] == "White"
        assert result["dimensions"] == "15 x 10 x 5 cm"
        assert result["weight"] == "350g"
        assert result["capacity"] == ""
        assert result["package_contents"] == "1 x Device, 1 x USB Cable"
        assert result["brand"] == "BeautyPro"
        assert result["target_audience"] == "Women 25-45"
        assert result["html_path"] == "html/B0GVYXC124.html"
        assert result["image_urls"] == "https://img1.jpg;https://img2.jpg"
        db.close()

    def test_stores_json_array_fields_as_python_lists(self):
        """JSON 数组字段存储后读回为 Python list"""
        import sqlite3
        from db import init_db, upsert_product, get_product

        db = sqlite3.connect(":memory:")
        init_db(db)

        upsert_product(db, SAMPLE_PRODUCT)
        result = get_product(db, "B0GVYXC124")

        assert isinstance(result["features"], list)
        assert result["features"] == ["Facial massage", "Body contouring", "5 modes"]
        assert isinstance(result["use_scenarios"], list)
        assert result["use_scenarios"] == ["Home spa", "Daily skincare"]
        assert isinstance(result["unique_selling_points"], list)
        assert result["unique_selling_points"] == ["5-in-1 multifunction", "Portable USB charging"]
        assert isinstance(result["en_search_keywords"], list)
        db.close()

    def test_stores_json_object_field_as_python_dict(self):
        """JSON 对象字段存储后读回为 Python dict"""
        import sqlite3
        from db import init_db, upsert_product, get_product

        db = sqlite3.connect(":memory:")
        init_db(db)

        upsert_product(db, SAMPLE_PRODUCT)
        result = get_product(db, "B0GVYXC124")

        assert isinstance(result["technical_specs"], dict)
        assert result["technical_specs"] == {"power": "5W", "battery": "2000mAh"}
        db.close()

    def test_upsert_updates_existing_product(self):
        """同一 ASIN 再次 upsert 时更新而非新增重复记录"""
        import sqlite3
        from db import init_db, upsert_product, get_product

        db = sqlite3.connect(":memory:")
        init_db(db)

        upsert_product(db, SAMPLE_PRODUCT)

        # 更新标题
        updated = dict(SAMPLE_PRODUCT)
        updated["title"] = "Updated Face Sculpting Machine"
        updated["features"] = ["Updated feature"]
        upsert_product(db, updated)

        result = get_product(db, "B0GVYXC124")
        assert result["title"] == "Updated Face Sculpting Machine"
        assert result["features"] == ["Updated feature"]
        # 未更新的字段应保持不变
        assert result["asin"] == "B0GVYXC124"

        # 验证只有一条记录
        count = db.execute(
            "SELECT COUNT(*) FROM products WHERE asin = ?", ("B0GVYXC124",)
        ).fetchone()[0]
        assert count == 1, f"upsert 应更新而非插入新记录，但发现 {count} 条"
        db.close()

    def test_created_at_is_set_on_insert(self):
        """插入时自动设置 created_at 时间戳"""
        import sqlite3
        from db import init_db, upsert_product, get_product

        db = sqlite3.connect(":memory:")
        init_db(db)

        upsert_product(db, SAMPLE_PRODUCT)
        result = get_product(db, "B0GVYXC124")

        assert "created_at" in result
        assert result["created_at"], "created_at 不应为空"
        db.close()

    def test_get_nonexistent_product_returns_none(self):
        """读取不存在的 ASIN 返回 None"""
        import sqlite3
        from db import init_db, get_product

        db = sqlite3.connect(":memory:")
        init_db(db)

        result = get_product(db, "NONEXISTENT")
        assert result is None
        db.close()

    def test_minimal_product_with_defaults(self):
        """只提供 ASIN 的最简产品可正常插入和读取，其他字段使用默认值"""
        import sqlite3
        from db import init_db, upsert_product, get_product

        db = sqlite3.connect(":memory:")
        init_db(db)

        upsert_product(db, {"asin": "B0MINIMAL"})
        result = get_product(db, "B0MINIMAL")

        assert result is not None
        assert result["asin"] == "B0MINIMAL"
        assert result["title"] == ""
        assert result["features"] == []
        assert result["technical_specs"] == {}
        assert result["use_scenarios"] == []
        assert result["unique_selling_points"] == []
        assert result["en_search_keywords"] == []
        db.close()


# ============================================================
# 切片3: upsert_translation() + get_translation() — 翻译 CRUD
# ============================================================

SAMPLE_TRANSLATION = {
    "asin": "B0GVYXC124",
    "russian_title": "массажер для лица 5 в 1",
    "core_keywords": "массажер для лица лифтинг аппарат",
    "russian_description": "Многофункциональный массажер для лица и тела.",
    "phase1_model": "gpt-4o",
    "phase2_model": "claude-opus-4-8",
}


class TestUpsertTranslation:
    """upsert_translation() 插入/更新翻译记录。"""

    def test_inserts_new_translation(self):
        """插入新翻译后可通过 get_translation 读取"""
        import sqlite3
        from db import init_db, upsert_product, upsert_translation, get_translation

        db = sqlite3.connect(":memory:")
        init_db(db)
        # 先插入产品（外键约束）
        upsert_product(db, {"asin": "B0GVYXC124"})

        upsert_translation(db, SAMPLE_TRANSLATION)
        result = get_translation(db, "B0GVYXC124")

        assert result is not None
        assert result["asin"] == "B0GVYXC124"
        assert result["russian_title"] == "массажер для лица 5 в 1"
        assert result["core_keywords"] == "массажер для лица лифтинг аппарат"
        assert result["russian_description"] == "Многофункциональный массажер для лица и тела."
        assert result["phase1_model"] == "gpt-4o"
        assert result["phase2_model"] == "claude-opus-4-8"
        db.close()

    def test_upsert_updates_existing_translation(self):
        """同一 ASIN 再次 upsert 时更新翻译记录"""
        import sqlite3
        from db import init_db, upsert_product, upsert_translation, get_translation

        db = sqlite3.connect(":memory:")
        init_db(db)
        upsert_product(db, {"asin": "B0GVYXC124"})

        upsert_translation(db, SAMPLE_TRANSLATION)

        # 更新
        updated = dict(SAMPLE_TRANSLATION)
        updated["russian_title"] = "обновленный массажер"
        updated["phase2_model"] = "gpt-5"
        upsert_translation(db, updated)

        result = get_translation(db, "B0GVYXC124")
        assert result["russian_title"] == "обновленный массажер"
        assert result["phase2_model"] == "gpt-5"
        # 未更新的字段保持不变
        assert result["core_keywords"] == "массажер для лица лифтинг аппарат"

        # 验证只有一条记录
        count = db.execute(
            "SELECT COUNT(*) FROM translations WHERE asin = ?", ("B0GVYXC124",)
        ).fetchone()[0]
        assert count == 1
        db.close()

    def test_created_at_is_set(self):
        """插入时自动设置 created_at"""
        import sqlite3
        from db import init_db, upsert_product, upsert_translation, get_translation

        db = sqlite3.connect(":memory:")
        init_db(db)
        upsert_product(db, {"asin": "B0GVYXC124"})

        upsert_translation(db, SAMPLE_TRANSLATION)
        result = get_translation(db, "B0GVYXC124")

        assert "created_at" in result
        assert result["created_at"], "created_at 不应为空"
        db.close()

    def test_get_nonexistent_translation_returns_none(self):
        """读取不存在的翻译返回 None"""
        import sqlite3
        from db import init_db, get_translation

        db = sqlite3.connect(":memory:")
        init_db(db)

        result = get_translation(db, "NONEXISTENT")
        assert result is None
        db.close()

    def test_minimal_translation_with_defaults(self):
        """只提供 ASIN 的最简翻译可正常插入，其他字段使用默认值"""
        import sqlite3
        from db import init_db, upsert_product, upsert_translation, get_translation

        db = sqlite3.connect(":memory:")
        init_db(db)
        upsert_product(db, {"asin": "B0MINIMAL"})

        upsert_translation(db, {"asin": "B0MINIMAL"})
        result = get_translation(db, "B0MINIMAL")

        assert result is not None
        assert result["asin"] == "B0MINIMAL"
        assert result["russian_title"] == ""
        assert result["core_keywords"] == ""
        assert result["russian_description"] == ""
        assert result["phase1_model"] == ""
        assert result["phase2_model"] == ""
        db.close()


# ============================================================
# 切片4: get_all_products() + get_products_pending_phase2()
# ============================================================

class TestGetAllProducts:
    """get_all_products() 列出全部产品。"""

    def test_returns_all_products(self):
        """插入多条产品后返回全部记录列表"""
        import sqlite3
        from db import init_db, upsert_product, get_all_products

        db = sqlite3.connect(":memory:")
        init_db(db)

        upsert_product(db, {"asin": "ASIN001", "title": "Product A"})
        upsert_product(db, {"asin": "ASIN002", "title": "Product B"})
        upsert_product(db, {"asin": "ASIN003", "title": "Product C"})

        results = get_all_products(db)
        assert len(results) == 3
        asins = {r["asin"] for r in results}
        assert asins == {"ASIN001", "ASIN002", "ASIN003"}
        db.close()

    def test_returns_empty_list_when_no_products(self):
        """无产品时返回空列表"""
        import sqlite3
        from db import init_db, get_all_products

        db = sqlite3.connect(":memory:")
        init_db(db)

        results = get_all_products(db)
        assert results == []
        db.close()

    def test_returns_deserialized_json_fields(self):
        """返回的产品包含反序列化的 JSON 字段"""
        import sqlite3
        from db import init_db, upsert_product, get_all_products

        db = sqlite3.connect(":memory:")
        init_db(db)

        upsert_product(db, SAMPLE_PRODUCT)
        results = get_all_products(db)

        assert len(results) == 1
        assert isinstance(results[0]["features"], list)
        assert isinstance(results[0]["technical_specs"], dict)
        db.close()


class TestGetProductsPendingPhase2:
    """get_products_pending_phase2() 返回有产品但无翻译的 ASIN。"""

    def test_returns_asins_without_translation(self):
        """有产品记录但无翻译记录的 ASIN 被正确返回"""
        import sqlite3
        from db import (
            init_db, upsert_product, upsert_translation,
            get_products_pending_phase2,
        )

        db = sqlite3.connect(":memory:")
        init_db(db)

        # 插入 3 个产品
        upsert_product(db, {"asin": "A001", "title": "Has translation"})
        upsert_product(db, {"asin": "A002", "title": "No translation"})
        upsert_product(db, {"asin": "A003", "title": "Another no translation"})

        # 只为 A001 创建翻译
        upsert_translation(db, {"asin": "A001", "russian_title": "есть перевод"})

        pending = get_products_pending_phase2(db)
        assert set(pending) == {"A002", "A003"}
        db.close()

    def test_returns_empty_when_all_translated(self):
        """所有产品都有翻译时返回空列表"""
        import sqlite3
        from db import (
            init_db, upsert_product, upsert_translation,
            get_products_pending_phase2,
        )

        db = sqlite3.connect(":memory:")
        init_db(db)

        upsert_product(db, {"asin": "A001"})
        upsert_product(db, {"asin": "A002"})
        upsert_translation(db, {"asin": "A001"})
        upsert_translation(db, {"asin": "A002"})

        pending = get_products_pending_phase2(db)
        assert pending == []
        db.close()

    def test_returns_empty_when_no_products(self):
        """无任何产品时返回空列表"""
        import sqlite3
        from db import init_db, get_products_pending_phase2

        db = sqlite3.connect(":memory:")
        init_db(db)

        pending = get_products_pending_phase2(db)
        assert pending == []
        db.close()

    def test_returns_all_when_none_translated(self):
        """所有产品都无翻译时返回全部 ASIN"""
        import sqlite3
        from db import init_db, upsert_product, get_products_pending_phase2

        db = sqlite3.connect(":memory:")
        init_db(db)

        upsert_product(db, {"asin": "A001"})
        upsert_product(db, {"asin": "A002"})

        pending = get_products_pending_phase2(db)
        assert set(pending) == {"A001", "A002"}
        db.close()
