# WB Content Tool

跨境电商 Wildberries 文案自动化工具：亚马逊 ASIN 采集 + AI 翻译 EN→RU。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 配置 API Key

复制 `.env.template` 为 `.env`，填入你的 AI API Key：

```
TRANSLATE_API_PROVIDER=openai        # openai / anthropic / deepseek / custom
TRANSLATE_API_KEY=sk-your-key-here
TRANSLATE_MODEL=gpt-4o
```

### 3. 启动

**Windows**：双击 `run.bat`

**命令行**：
```bash
streamlit run app.py
```

浏览器打开 http://localhost:8501

---

## 使用流程

### 功能区 1：爬虫采集

1. 准备一个 Excel 文件，第一列名为 `asin`，填入要采集的 ASIN
2. 打开 Web 页面，选择 **🕷️ 爬虫采集** Tab
3. 上传 Excel → 显示 "检测到 X 个 ASIN"
4. 点击 **开始采集** → 等待进度完成
5. 预览结果 → 点击 **下载处理前表格**

### 功能区 2：文案翻译

1. 在左侧边栏配置 AI API（服务商、Key、模型）
2. 选择 **📝 文案翻译** Tab
3. 上传刚下载的"处理前"表格
4. 点击 **开始翻译** → 等待进度完成
5. 预览结果 → 点击 **下载处理后表格**

"处理后"表格可直接导入店小秘进行 WB 发布。

---

## 命令行用法（可选）

跳过 Web 界面，直接在终端运行：

```bash
# 爬虫：ASIN Excel → 处理前表格
python crawler.py --input asin列表.xlsx --output 处理前.xlsx

# 翻译：处理前表格 → 处理后表格
python translator.py --input 处理前.xlsx --output 处理后.xlsx
```

---

## 项目结构

```
├── app.py                 # Web 界面入口
├── crawler.py             # 爬虫模块（Playwright）
├── crawler_ui.py          # 爬虫 Web 业务逻辑
├── translator.py          # 翻译模块（AI API）
├── translator_ui.py       # 翻译 Web 业务逻辑
├── excel_io.py            # Excel 读写公共模块
├── extractor.py           # HTML 信息提取器
├── config.py              # 配置管理
├── prompts/
│   └── translation_persona.txt  # AI 翻译人设 Prompt
├── tests/                 # 测试
├── issues/                # 需求文档
└── CONTEXT.md             # 领域上下文
```
