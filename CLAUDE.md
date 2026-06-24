# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

WB Content Tool — 跨境电商 Amazon→Wildberries 文案自动化工具。Streamlit Web 应用，从亚马逊采集产品信息，通过两阶段 AI 流水线生成俄语 SEO 文案，支持图片本地化翻译和 AI 产品卡片设计。

## 常用命令

```bash
# 启动 Web 界面（Windows 也可双击 run.bat）
streamlit run app.py

# 命令行模式 — 爬虫
python crawler.py --input asin列表.xlsx --output 处理前.xlsx

# 命令行模式 — 翻译
python translator.py --input 处理前.xlsx --output 处理后.xlsx

# 运行所有测试
pytest tests/ -v

# 运行单个测试文件
pytest tests/test_workflow_engine.py -v

# 安装依赖
pip install -r requirements.txt
playwright install chromium

# 构建分发包
python build.py
```

## 核心架构

### 两阶段 AI 翻译流水线

```
Amazon HTML → Phase 1 (信息萃取) → SQLite (products.db) → Phase 2 (文案生成) → 俄语 SEO 文案
```

- **Phase 1** (`phase1_extractor.py`): AI 从原始 HTML 提取结构化产品记录（品类、材质、功能、卖点等 15+ 字段），写入 `products` 表
- **Phase 2** (`phase2_translator.py`): AI 基于结构化记录生成俄语标题、核心流量词、详情，写入 `translations` 表
- **中间数据库** (`db.py`): SQLite 单文件，`products` 和 `translations` 两表以 ASIN 关联。Phase 1 和 Phase 2 解耦，支持人工审查修正后再进入 Phase 2
- Phase 1/2 可独立配置不同的 AI 服务商和模型（`PHASE1_*` / `PHASE2_*` 环境变量，未设置时 fallback 到旧 `TRANSLATE_*` 配置）

### 图片翻译全链路 (Tab3)

```
下载原图 → PaddleOCR 检测定位 → DeepSeek 翻译 → AI 修复擦除 → Pillow 覆写俄文 → 3:4 尺寸转换 → 上传 R2 + 本地存档
```

- PaddleOCR 不可替代（必须输出文字坐标才能定位擦除位置）
- 双管线：传统管线（OCR+覆写）和 AI 管线（Gemini 直译/中转站）
- 图片存本地 `images/{ASIN}/` + Cloudflare R2 双份
- 容错设计：单张图失败不影响同 ASIN 其他图

### UI/Worker 分离架构

- `worker.py`: 后台 Worker 管理器（`WorkerManager`），daemon 线程执行耗时任务
- `progress.json`: 断点续跑和进度恢复，使用 `_atomic_write_json`（先写 tmp 再 `os.replace`）保证不损坏
- 步骤级指数退避重试（默认 3 次），关闭浏览器后任务继续运行

### 工作流引擎 (Tab5)

- `workflow_engine.py`: `WorkflowRunner` 编排 5 阶段流水线（CRAWL → PHASE1 → PHASE2 → IMAGE_TRANSLATION → IMAGE_CARD_DESIGN），每阶段可独立启停
- `workflow_ui.py`: 可视化编辑器，新建/编辑/保存/运行工作流配置

### 配置系统 (`config.py`)

- `Settings` 类从 `.env` 读取所有配置，模块级 `settings` 单例供全局使用
- 首次启动时 `.env` 不存在则自动创建（带默认值）
- `settings.save_to_env()` 从 Web 侧边栏持久化配置
- 配置优先级链：Phase 2 ← Phase 1 ← TRANSLATE_*（向后兼容旧配置名）

### 关键外部依赖

| 服务 | 用途 |
|------|------|
| DeepSeek / OpenAI 兼容 API | 文字翻译、图片文字翻译 |
| Replicate | AI 图片修复（文字擦除） |
| ScraperAPI | 付费代理爬取亚马逊（主方案） |
| Playwright | 浏览器自动化爬取（备用方案） |
| PaddleOCR | 本地 OCR 文字检测定位 |
| Cloudflare R2 | 图片对象存储（S3 兼容，通过 boto3） |
| Gemini | AI 产品卡片图片生成 |

### AI Prompt 管理

所有 Prompt 文件在 `prompts/` 目录，独立 persona 文件：
- `phase1_extraction_persona.txt` — Phase 1 信息萃取
- `phase2_translation_persona.txt` — Phase 2 文案生成（含蜘蛛网关键词布局策略）
- `translation_persona.txt` — 旧版单阶段翻译（保留兼容）
- `image_translation_persona.txt` — 图片文字翻译（精准直译，非 SEO 风格）
- `gemini_card_design_persona.txt` — Gemini 产品卡片设计

### 领域术语（详见 `CONTEXT.md`）

- **ASIN**: 亚马逊标准识别码，爬虫唯一输入标识
- **Listing**: 全流程产品信息单元（源 Listing→目标 Listing）
- **核心流量词**: AI 生成的俄语搜索关键词，用于 WB 搜索排名
- **蜘蛛网关键词布局**: 三层 SEO 策略（核心区/说服区/补充区）
- **WB**: Wildberries，俄罗斯最大电商平台，目标发布平台

### 文件被 .gitignore 排除的运行时产物

`progress.json`, `card_progress.json`, `products.db`, `html/`, `images/`, `r2_storage.py` — 这些是运行时生成的文件，不入库。`r2_storage.py` 是配置驱动的 R2 上传模块，由 build.py 从 `r2_storage.py.template` 或运行时动态生成。
