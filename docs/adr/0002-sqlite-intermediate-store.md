# 使用 SQLite 作为产品信息中间数据层

在爬虫和文案生成之间引入 SQLite 单文件数据库（`products.db`），存储 Phase 1 产出的结构化产品记录和 Phase 2 的翻译结果。

## 为什么

- **两表关联**：`products`（15+ 属性字段）和 `translations`（3 文案字段）以 ASIN 主键一对一关联，天然支持查询和 join。
- **支持人工审查**：用户可通过 sqlite3 CLI 或 Streamlit Web UI 直接查看/修改记录，比 Excel 或 JSON 更结构化。
- **零运维**：SQLite 是单文件、无需服务进程、Python 标准库直接支持。与项目的 Streamlit 单机部署模型一致。

## 考虑的替代方案

- **Excel 扩展列**：与现有流程最兼容，但多列嵌套（JSON 数组字段如 features、use_scenarios）在 Excel 中难以编辑和查询。
- **JSON 文件（products/{ASIN}/info.json）**：与图片目录自然共存，但跨 ASIN 查询（如"列出所有材质为玻璃的产品"）需要遍历文件系统，效率低。
- **PostgreSQL**：功能最强但在单机 Streamlit 场景下过度设计，引入运维负担。
