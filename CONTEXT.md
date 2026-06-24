# WB Content Tool — 领域上下文

## 术语表

### Listing（商品信息单元）

从亚马逊采集到 Wildberries 发布全流程中的产品信息集合。包含：

- **源 Listing**：从亚马逊爬取的原始英文商品信息（asin、标题、详情、图片 URL）
- **目标 Listing**：翻译并优化后的俄文商品信息（含核心流量词、俄语标题、俄语详情），可直接用于 WB 平台发布

### 爬虫表格（处理前）

爬虫输出的 Excel 表格，4 列：`asin`、`图片url`、`标题`、`详情`。所有字段为英文原文。

### 爬虫表格（处理后）

AI 翻译输出的 Excel 表格，12 列：`asin`、`图片url`、`标题`、`详情`、`核心流量词`、`俄语标题`、`俄语详情`、`货源`(空)、`采购价`(空)、_(空)_、_(空)_、`商品类别`(空)。前 4 列保留英文原文，后 3 列为 AI 生成的俄语 SEO 优化文案。

### 核心流量词

AI 为产品生成的核心搜索关键词（俄语），用于 WB 平台搜索排名优化。是 SEO 关键词布局中"核心区"的一部分。

### 蜘蛛网关键词布局

翻译 AI 人设中定义的关键词优化策略，分三层：
- **核心区（标题）**：≤60 字符，含核心关键词，最大化曝光
- **说服区（要点描述）**：关键词融入场景化卖点，促进转化
- **补充区（后台词/ST）**：长尾词组合，扩大搜索覆盖

### WB（Wildberries）

俄罗斯最大电商平台（wildberries.ru），本系统的目标发布平台。

### Phase 1（AI 信息萃取）

两阶段翻译流水线的第一阶段。AI 接收亚马逊产品页 HTML，输出结构化产品记录（品类、材质、功能、卖点、目标用户等 15 个字段），写入产品信息数据库。**已替代** `extractor.py` 的确定性 HTML 解析。
_Avoid_: 提取阶段、萃取阶段、预处理

### Phase 2（AI 文案生成）

两阶段翻译流水线的第二阶段。AI 接收 Phase 1 产出的结构化产品记录，生成面向 Wildberries 的俄语 SEO 文案（标题 / 核心流量词 / 详情）。输出 3 字段格式不变，但内容因输入信息完整性而更精准。
_Avoid_: 翻译阶段、生成阶段

### 产品信息数据库

SQLite 单文件数据库（`products.db`），是 Phase 1 和 Phase 2 之间的中间数据层。包含 `products` 表（结构化属性）和 `translations` 表（俄语文案），以 ASIN 关联。支持人工审查和修正。
_Avoid_: 中间表、临时数据、Excel 中间态

### 结构化产品记录

Phase 1 输出的完整产品属性集，包含：标识信息（asin、品类）、基础属性（材质/颜色/尺寸/重量/容量/包装）、功能特性（功能列表/技术参数）、营销维度（目标用户/使用场景/差异化卖点/品牌）、SEO 预备数据（英文搜索词）。与"源 Listing"的对比：源 Listing 只有标题和详情，结构化记录将其拆解为机器可读的独立字段。
_Avoid_: 属性表、字段映射

### ASIN

亚马逊标准识别码（Amazon Standard Identification Number）。爬虫唯一的输入标识，通过 `https://www.amazon.com/dp/{ASIN}` 访问产品页。

## 架构决策

### 爬虫方案：ScraperAPI（主）+ Playwright（备用）

- **ScraperAPI 模式**（推荐）：通过付费代理服务访问亚马逊，自动处理 IP 轮换、CAPTCHA 绕过。每请求消耗 5 credits。通过 `httpx` HTTP 请求调用，无需浏览器。
- **Playwright 模式**（备用）：使用 Playwright 异步 API 模拟真实浏览器直接访问亚马逊。需系统已安装 Chrome，且需配合 VPN 使用。反爬策略：随机 User-Agent、请求间隔（3-8 秒）、模拟滚动、失败重试 2 次。
- 用户可在 Streamlit 侧边栏切换采集模式，配置持久化到 `.env`。

### 翻译方案：两阶段 AI 流水线

- **Phase 1（信息萃取）**：将亚马逊产品页 HTML 交给 AI，提取完整结构化属性（品类、材质、功能、场景、目标用户等 15 个字段），写入 SQLite 数据库。替代了原有的确定性 `extractor.py` 解析。
- **Phase 2（文案生成）**：基于 Phase 1 产出的结构化产品记录，由独立 persona 生成俄语 SEO 文案（标题、核心流量词、详情）。不再依赖原始 HTML。
- **两阶段可独立配置 AI 服务商和模型**，支持 Phase 1 用高性价比模型批量萃取、Phase 2 用强模型精细写文案。
- 中间态（SQLite 数据库）可人工审查和修正，再进入 Phase 2。
- System Prompt 从 `prompts/` 目录加载，Phase 1 和 Phase 2 使用独立的 persona 文件。

### 产品信息数据库

- **SQLite 单文件**（`products.db`），存储 Phase 1 萃取的结构化产品记录和 Phase 2 的翻译结果。
- **两表结构**：`products`（原始属性 15+ 字段）和 `translations`（俄语文案 3 字段），以 ASIN 为主键关联。
- Phase 1 输入原始 HTML 本地存档（`html/{ASIN}.html`），数据库只存路径引用。

### 运行方式：Streamlit Web 界面

- 三个独立 Tab：爬虫采集 + 文案翻译 + 图片翻译
- 支持分步操作和进度反馈
- 侧边栏提供 API 配置（翻译、R2、图片处理参数）

### 图片翻译：全流程本地化（Tab3）

将亚马逊商品图从英文→俄语本地化，完整链路：

```
下载原图 → PaddleOCR 检测定位 → DeepSeek 翻译 → AI 修复擦除英文 → Pillow 写入俄文 → 改尺寸 3:4 → 上传 R2 + 本地存档
```

**核心决策：**
- OCR 引擎：PaddleOCR（必须输出文字坐标，否则无法定位擦除/覆写位置）
- 翻译：DeepSeek API，使用独立 Prompt `prompts/image_translation_persona.txt`（精准直译，非 SEO 优化）
- 文字擦除：AI 修复模型 API（如 Lama Cleaner on Replicate），非白底图需高质量背景恢复
- 俄文覆写：Pillow，字体/字号/颜色做成可配置模块
- 尺寸转换：3:4（900×1200），等比缩放+白边填充，绝不拉伸变形。resize_mode 配置项（pad 当前 / outpainting 未来升级）
- 无文字图片：跳过翻译，只改尺寸 + 上传 R2
- 存储：本地 `images/{ASIN}/` + Cloudflare R2 双存，分目录 + 处理标记命名（`01_ru.jpg`）
- 输入兼容：4 列（爬虫表）或 12 列（翻译后表）均可
- 输出：Excel（图片 URL 全替换为 R2 URL）+ 详细日志报告
- 容错：尽力而为（单张图失败不影响同 ASIN 其他图，失败图兜底改尺寸上传不写字）
- 断点续跑：progress.json 记录进度
- 并发：ASIN 间串行，ASIN 内图片并发
- UI：独立 Tab，含试跑预览功能（先处理 1 个 ASIN 展示前后对比）
- 后台运行：UI/Worker 分离，关闭浏览器后继续处理。步骤级指数退避重试（3 次），progress.json 支持断点续跑和进度轮询恢复

## 外部系统

### 外部 AI 服务

- **DeepSeek**（deepseek.com）：文字翻译 + 图片文字翻译，OpenAI 兼容 API
- **Replicate**（replicate.com）：托管 AI 修复模型（Lama Cleaner），用于图片文字擦除

### ScraperAPI

付费代理服务（scraperapi.com），用于爬取亚马逊产品页。每请求 5 credits，Hobby $49/月（100K credits）、Startup $149/月（500K-1M credits）。

### PaddleOCR

百度开源 OCR 引擎，本地运行（CPU），输出文字坐标和原文。是图片翻译链条中不可替代的定位环节。

### Cloudflare R2

对象存储服务，用于托管翻译后的俄语商品图片。S3 兼容协议，通过 `boto3` 访问。

- 免费额度：10GB 存储 + 无流量费
- 配置项：R2_ACCESS_KEY_ID、R2_SECRET_ACCESS_KEY、R2_ACCOUNT_ID、R2_BUCKET、R2_PUBLIC_DOMAIN

### 店小秘（Dianxiaomi）

跨境电商 ERP 工具。当前在本系统中不直接对接——用户手动将"处理后"表格导入店小秘进行发布。
