"""
产品信息数据库模块 — SQLite 中间数据层。

存储 Phase 1（AI 信息萃取）产出的结构化产品记录
和 Phase 2（AI 文案生成）产出的俄语翻译结果。

公共接口：
    init_db              — 创建表结构
    upsert_product       — 插入/更新产品记录
    get_product          — 读取产品记录
    upsert_translation   — 插入/更新翻译记录
    get_translation      — 读取翻译记录
    get_all_products     — 列出全部产品
    get_products_pending_phase2 — 返回有产品但无翻译的 ASIN 列表
"""

import json
import sqlite3
from datetime import datetime, timezone


def init_db(db: sqlite3.Connection) -> None:
    """创建 products 和 translations 表（如不存在）。

    Args:
        db: sqlite3 连接对象（可以是 :memory: 或文件路径连接）。
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS products (
            asin TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            details TEXT NOT NULL DEFAULT '',
            image_urls TEXT NOT NULL DEFAULT '',
            html_path TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            material TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT '',
            dimensions TEXT NOT NULL DEFAULT '',
            weight TEXT NOT NULL DEFAULT '',
            capacity TEXT NOT NULL DEFAULT '',
            package_contents TEXT NOT NULL DEFAULT '',
            features TEXT NOT NULL DEFAULT '[]',
            technical_specs TEXT NOT NULL DEFAULT '{}',
            target_audience TEXT NOT NULL DEFAULT '',
            use_scenarios TEXT NOT NULL DEFAULT '[]',
            unique_selling_points TEXT NOT NULL DEFAULT '[]',
            brand TEXT NOT NULL DEFAULT '',
            en_search_keywords TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS translations (
            asin TEXT PRIMARY KEY,
            russian_title TEXT NOT NULL DEFAULT '',
            core_keywords TEXT NOT NULL DEFAULT '',
            russian_description TEXT NOT NULL DEFAULT '',
            phase1_model TEXT NOT NULL DEFAULT '',
            phase2_model TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    db.commit()


# JSON 数组字段（存储为 TEXT，读写时序列化/反序列化）
_JSON_ARRAY_FIELDS = {
    "features", "use_scenarios", "unique_selling_points", "en_search_keywords",
}

# JSON 对象字段
_JSON_OBJECT_FIELDS = {"technical_specs"}

# products 表全部字段
_PRODUCT_FIELDS = [
    "asin", "title", "details", "image_urls", "html_path",
    "category", "material", "color", "dimensions", "weight",
    "capacity", "package_contents", "features", "technical_specs",
    "target_audience", "use_scenarios", "unique_selling_points",
    "brand", "en_search_keywords",
]


def _serialize_product(product: dict) -> dict:
    """将 Python 对象序列化为 SQLite 可存储的格式。

    JSON 字段转为字符串，None 值转为默认值。
    """
    row = {}
    for field in _PRODUCT_FIELDS:
        value = product.get(field)
        if field in _JSON_ARRAY_FIELDS:
            row[field] = json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)
        elif field in _JSON_OBJECT_FIELDS:
            row[field] = json.dumps(value if isinstance(value, dict) else {}, ensure_ascii=False)
        else:
            row[field] = str(value) if value is not None else ""
    return row


def _deserialize_product(row: sqlite3.Row) -> dict:
    """将 SQLite 行转换为 Python dict，JSON 字段反序列化。"""
    result = dict(row)
    for field in _JSON_ARRAY_FIELDS:
        raw = result.get(field, "[]")
        try:
            result[field] = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
        except json.JSONDecodeError:
            result[field] = []
    for field in _JSON_OBJECT_FIELDS:
        raw = result.get(field, "{}")
        try:
            result[field] = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
        except json.JSONDecodeError:
            result[field] = {}
    return result


def upsert_product(db: sqlite3.Connection, product: dict) -> None:
    """插入或更新产品记录。

    Args:
        db: sqlite3 连接对象。
        product: 产品字典，asin 为必填字段，其他字段使用默认值。
    """
    row = _serialize_product(product)
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    updates = ", ".join(f"{col} = excluded.{col}" for col in row if col != "asin")

    db.execute(
        f"INSERT INTO products ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(asin) DO UPDATE SET {updates}",
        list(row.values()),
    )
    db.commit()


def get_product(db: sqlite3.Connection, asin: str) -> dict | None:
    """根据 ASIN 读取产品记录。

    Args:
        db: sqlite3 连接对象。
        asin: 亚马逊 ASIN 标识码。

    Returns:
        产品字典，若不存在则返回 None。
    """
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM products WHERE asin = ?", (asin,)).fetchone()
    db.row_factory = None
    if row is None:
        return None
    return _deserialize_product(row)


# translations 表全部字段（不含 created_at，由数据库自动处理）
_TRANSLATION_FIELDS = [
    "asin", "russian_title", "core_keywords", "russian_description",
    "phase1_model", "phase2_model",
]


def upsert_translation(db: sqlite3.Connection, translation: dict) -> None:
    """插入或更新翻译记录。

    Args:
        db: sqlite3 连接对象。
        translation: 翻译字典，asin 为必填字段，其他字段使用默认值。
    """
    row = {
        f: str(translation.get(f, "")) if translation.get(f) is not None else ""
        for f in _TRANSLATION_FIELDS
    }
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    updates = ", ".join(f"{col} = excluded.{col}" for col in row if col != "asin")

    db.execute(
        f"INSERT INTO translations ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(asin) DO UPDATE SET {updates}",
        list(row.values()),
    )
    db.commit()


def get_translation(db: sqlite3.Connection, asin: str) -> dict | None:
    """根据 ASIN 读取翻译记录。

    Args:
        db: sqlite3 连接对象。
        asin: 亚马逊 ASIN 标识码。

    Returns:
        翻译字典，若不存在则返回 None。
    """
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM translations WHERE asin = ?", (asin,)).fetchone()
    db.row_factory = None
    if row is None:
        return None
    return dict(row)


def get_all_products(db: sqlite3.Connection) -> list[dict]:
    """列出全部产品记录。

    Args:
        db: sqlite3 连接对象。

    Returns:
        产品字典列表（含反序列化的 JSON 字段）。无产品时返回空列表。
    """
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM products ORDER BY created_at DESC").fetchall()
    db.row_factory = None
    return [_deserialize_product(row) for row in rows]


def get_products_pending_phase2(db: sqlite3.Connection) -> list[str]:
    """返回有产品记录但尚无翻译记录的 ASIN 列表。

    用于 Phase 2 流水线确定哪些产品需要生成文案。

    Args:
        db: sqlite3 连接对象。

    Returns:
        ASIN 字符串列表。全部已翻译或无产品时返回空列表。
    """
    rows = db.execute(
        "SELECT p.asin FROM products p "
        "LEFT JOIN translations t ON p.asin = t.asin "
        "WHERE t.asin IS NULL "
        "ORDER BY p.asin"
    ).fetchall()
    return [row[0] for row in rows]
