# PRD: 跨境电商 Wildberries 文案自动化工具

**GitHub**：[#6](https://github.com/linhuanwen/wb-content-tool/issues/6)

## Problem Statement

跨境电商卖家在 Wildberries（俄罗斯电商平台）上架产品时，需要将从亚马逊采集的英文商品文案（标题、详情）以及产品图片中的英文文字，翻译为符合俄罗斯市场习惯的俄语文案。当前这一翻译环节完全依赖人工操作：卖家需要逐条复制英文文案、通过翻译工具翻译、再逐条粘贴回店小秘（跨境电商 ERP），同时还需要用 Photoshop 等工具手动修改产品图片中的英文文字。每个产品仅文案翻译和图片处理就需要 15-30 分钟，成为整个上架流程中最大的效率瓶颈。

## Solution

构建一个 Web 工具，实现"ASIN 输入 → 亚马逊爬虫采集英文信息 → AI 智能翻译为俄语 SEO 优化文案"的半自动化管线。工具分为两个独立功能区：

1. **爬虫采集**：上传仅含 ASIN 列的 Excel → 系统自动爬取亚马逊产品标题、图片 URL、详情 → 输出标准格式的"处理前"表格
2. **文案翻译**：上传"处理前"表格 → 系统调用 AI 按跨境电商本地化专家的人设，翻译为符合 Wildberries SEO 要求的俄语文案 → 输出包含 12 列的"处理后"表格

图片翻译功能暂不纳入本次范围，列入后续 backlog。

## User Stories

### 爬虫采集

1. As a 跨境电商卖家, I want to upload an Excel file containing only ASIN codes, so that the system can read my product selection list
2. As a 跨境电商卖家, I want the system to automatically crawl Amazon product pages by ASIN, so that I do not have to manually copy product information
3. As a 跨境电商卖家, I want the system to extract product titles in English, so that the original title is preserved for reference
4. As a 跨境电商卖家, I want the system to extract all product main images as URLs separated by semicolons, so that I can directly use them for Wildberries listing (which requires `;`-separated multi-image format)
5. As a 跨境电商卖家, I want the system to extract product details/descriptions in English, so that complete product information is available for translation
6. As a 跨境电商卖家, I want to see a progress bar while the crawler is running, so that I know how many ASINs have been processed and how many remain
7. As a 跨境电商卖家, I want the system to handle occasional failures gracefully (retry and log warnings), so that I do not lose all progress due to one failed ASIN
8. As a 跨境电商卖家, I want to download the crawled data as an Excel file, so that I can inspect the results before feeding them into translation
9. As a 跨境电商卖家, I want the output Excel format to match the existing "爬虫表格（处理前）" exactly, so that it fits seamlessly into my current workflow

### 文案翻译

10. As a 跨境电商卖家, I want to upload the "处理前" crawled Excel file, so that I can start translating the English content
11. As a 跨境电商卖家, I want the AI to translate English titles into Russian with SEO optimization, so that my products rank well on Wildberries search
12. As a 跨境电商卖家, I want the Russian title to be ≤ 60 characters, all lowercase, and free of special symbols, so that it complies with Wildberries platform best practices
13. As a 跨境电商卖家, I want the AI to generate core traffic keywords (核心流量词) in Russian for each product, so that I can optimize search visibility on Wildberries
14. As a 跨境电商卖家, I want the AI to translate English product details into Russian with keyword-rich descriptions, so that my product pages are compelling and searchable
15. As a 跨境电商卖家, I want brand names to be automatically removed from the translated content, so that I avoid trademark issues
16. As a 跨境电商卖家, I want the translation to follow the "spider web keyword layout" strategy (核心区 → 说服区 → 补充区), so that my listings are fully SEO-optimized
17. As a 跨境电商卖家, I want to see a progress bar while translation is running, so that I know the processing status
18. As a 跨境电商卖家, I want to download the translated data as an Excel file with 12 columns, so that it matches my existing "爬虫表格（处理后）" format
19. As a 跨境电商卖家, I want columns for 货源 (supplier link), 采购价 (purchase price), and 商品类别 (product category) to be left empty in the output, so that I can fill them in manually later

### API 配置

20. As a 跨境电商卖家, I want to configure my own AI API key and choose from different AI providers (Claude, OpenAI, DeepSeek, custom), so that I have flexibility and control over translation quality and cost
21. As a 跨境电商卖家, I want my API configuration to be saved locally, so that I do not have to re-enter it every time I use the tool
22. As a 跨境电商卖家, I want the system to give me a clear error message when the API key is not configured, so that I know what to fix

### 通用

23. As a 跨境电商卖家, I want the tool to run as a local web application, so that I can use it from my browser without installing complex software
24. As a 跨境电商卖家, I want the crawler and translator to work independently, so that I can run either step without depending on the other
25. As a 跨境电商卖家, I want the UI to be in Chinese (my native language), so that I can navigate the tool comfortably
