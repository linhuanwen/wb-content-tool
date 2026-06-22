# PRD: 图片翻译（全流程本地化）

**父 PRD**：[PRD: 跨境电商 Wildberries 文案自动化工具](./PRD.md)

## Problem Statement

跨境电商卖家将产品从亚马逊搬运到 Wildberries 时，产品图片中常常带有英文文字（产品名称、功能标注、参数说明、材质信息等）。俄罗斯消费者看到英文图片会产生不信任感，降低转化率。当前卖家只能用 Photoshop 逐张手动修改图片，每个产品通常 5-8 张图，纯手工修改一套图片需要 10-20 分钟。对于日均上架几十个产品的卖家，这是翻译文字之外的又一大效率瓶颈。

此外，Wildberries 平台要求图片尺寸为 3:4（宽:高），而亚马逊原图通常是 1:1 正方形，卖家需要额外调整所有图片尺寸。

## Solution

在现有「爬虫采集」和「文案翻译」之外，新增第三个功能区「图片翻译」，实现完整的图片本地化管线：

```
下载原图 → PaddleOCR 检测定位英文文字 → DeepSeek 翻译为俄语 → AI 修复模型擦除英文 → Pillow 覆写俄文 → 改尺寸 3:4（900×1200）→ 上传 Cloudflare R2 获取新 URL + 本地存档
```

核心原则：
- **所有图片都改尺寸**：无论是否有文字，统一调整为 3:4（900×1200）
- **有文字才翻译**：OCR 检测到英文文字时走完整翻译管线，无文字图只改尺寸
- **尽力而为 + 兜底**：单张图任意环节失败时，跳过翻译但照样改尺寸上传（不写字），不阻塞同产品其他图片
- **双端存储**：翻译后的图片本地留存 + 上传 R2 获取 URL，Excel 输出全部替换为 R2 URL

## User Stories

### 输入与试跑

1. As a 跨境电商卖家, I want to upload either a 4-column crawler output Excel or a 12-column translated Excel, so that I can do image translation at any point in my workflow
2. As a 跨境电商卖家, I want the system to automatically detect and list all ASINs and image counts from my uploaded file, so that I know the processing scope before starting
3. As a 跨境电商卖家, I want to preview-translate just one ASIN first (with side-by-side before/after comparison), so that I can verify image quality and font settings before committing to the full batch
4. As a 跨境电商卖家, I want to adjust font settings (font file, size, color) and see them applied in the preview, so that I can fine-tune the visual output

### 核心处理

5. As a 跨境电商卖家, I want the system to automatically download all images from Amazon URLs, so that I do not have to manually save each image
6. As a 跨境电商卖家, I want PaddleOCR to detect and locate all English text regions on each image, so that only text-bearing images go through translation
7. As a 跨境电商卖家, I want images without any English text to skip translation (only resize + upload), so that I do not waste API calls on text-free images
8. As a 跨境电商卖家, I want each detected text fragment to be translated into Russian by DeepSeek with precise and concise results, so that label-like text remains compact and accurate
9. As a 跨境电商卖家, I want the translation of image text to be direct/accurate (not SEO-optimized), so that technical parameters and product labels are correctly preserved
10. As a 跨境电商卖家, I want the AI repair model to seamlessly erase the original English text from the image background, so that the Russian text can be overlaid without ugly artifacts
11. As a 跨境电商卖家, I want the translated Russian text to be written onto the image in the position where the English text was erased, so that the layout looks natural
12. As a 跨境电商卖家, I want the font configuration (typeface, size, color) to be adjustable, so that I can match different product image styles
13. As a 跨境电商卖家, I want all images resized to 3:4 ratio (900×1200), so that they comply with Wildberries platform requirements
14. As a 跨境电商卖家, I want the resize strategy to be configurable (white padding for now, AI outpainting in the future), so that I can upgrade without changing the workflow

### 存储与输出

15. As a 跨境电商卖家, I want every processed image saved locally in organized folders (by ASIN, with processing markers in filenames), so that I have a local backup of all translated images
16. As a 跨境电商卖家, I want translated images uploaded to Cloudflare R2 and receive publicly accessible URLs, so that I can use them directly in my Wildberries listings
17. As a 跨境电商卖家, I want the output Excel to replace all image URLs with the new R2 URLs, so that I do not need to manually update image links
18. As a 跨境电商卖家, I want a detailed processing report (as a separate downloadable file) showing which images succeeded, failed, or were skipped, so that I can manually handle any failures

### 容错与可靠性

19. As a 跨境电商卖家, I want the failure of a single image to not block the processing of other images in the same ASIN, so that I get a mostly-complete result even if some images fail
20. As a 跨境电商卖家, I want failed images to still be resized and uploaded to R2 (without text modification), so that all images get a new R2 URL instead of Amazon's potentially expiring URL
21. As a 跨境电商卖家, I want images that fail to download from Amazon to be clearly marked in the report with their original URLs preserved, so that I can manually source them
22. As a 跨境电商卖家, I want a breakpoint-resume mechanism (progress.json), so that if my computer or browser is interrupted during a long batch, I can continue from where I left off without reprocessing completed ASINs
23. As a 跨境电商卖家, I want the system to process multiple images within the same ASIN concurrently, so that the overall batch completes faster
24. As a 跨境电商卖家, I want the Worker to continue processing in the background even after I close the browser page, so that I don't have to keep my browser open for a 2-3 hour batch job
25. As a 跨境电商卖家, I want to reopen the page and see the current progress automatically restored, so that I can check on long-running tasks without keeping track of what was running
26. As a 跨境电商卖家, I want to pause and resume the Worker, so that I can temporarily free up bandwidth or CPU for other tasks
27. As a 跨境电商卖家, I want each processing step (download/OCR/translate/repair/upload) to automatically retry up to 3 times with increasing delays on network failure, so that transient network issues don't cause permanent failures
28. As a 跨境电商卖家, I want the progress to be visible in real-time via polling (refreshing every 2 seconds), so that I can monitor the batch without manual refreshes

### 配置与管理

29. As a 跨境电商卖家, I want to configure R2 credentials in the sidebar, so that I can set up storage once and reuse it
30. As a 跨境电商卖家, I want the local image save path to be configurable, so that different computers can use different storage locations
31. As a 跨境电商卖家, I want to configure the AI repair model API key separately from the translation API, so that I have flexibility in choosing service providers
32. As a 跨境电商卖家, I want to configure retry count and step timeout values, so that I can tune resilience for my network conditions

## Implementation Decisions

### 新增模块

- **image_translator.py** — 图片翻译核心管道。单张图六步串行（下载→OCR→翻译→擦除→覆写→改尺寸→上传），ASIN 内图片并发（asyncio.gather），ASIN 间串行。每个步骤包装 `_retry_step()` 指数退避重试。支持断点续跑（progress.json）
- **image_translator_ui.py** — UI 逻辑层。文件上传校验（兼容 4/12 列）、翻译编排、输出 Excel 生成、处理报告生成
- **image_processor.py** — 图片编辑纯函数。文字擦除（调用 AI 修复 API）、俄文覆写（Pillow）、尺寸转换（pad/outpainting）。FontConfig 作为可配置数据类
- **r2_storage.py** — Cloudflare R2 对象存储封装。基于 boto3 S3 兼容协议，提供 upload/delete 操作
- **worker.py** — 后台任务管理器（WorkerManager 单例）。UI/Worker 分离，启动独立 asyncio task，不依赖 Streamlit WebSocket。支持 start/pause/resume/get_status，progress.json 原子写入

### 修改模块

- **config.py** — 新增配置项：R2 凭证（5 项）、图片处理参数（本地路径、尺寸模式、输出分辨率）、AI 修复 API 配置
- **app.py** — 新增 Tab 3「🖼️ 图片翻译」、侧边栏补充 R2/图片配置、试跑预览区域（原图 vs 俄语图并排展示）
- **excel_io.py** — 如需新增输出格式（图片翻译后的 Excel URL 结构）
- **build.py** — 打包时包含新模块 + 字体文件
- **requirements.txt** — 新增：paddleocr、paddlepaddle、boto3、Pillow、replicate

### 新增 Prompt

- **prompts/image_translation_persona.txt** — 独立于文案翻译人设。规则：精准直译、俄语长度 ≤ 原文 1.3 倍、技术参数精确保留、不进行 SEO 改写

### 架构决策

- **OCR 必须输出坐标**：选择 PaddleOCR（而非 AI Vision API）的核心原因是坐标——擦除和覆写需要知道文字的精确像素位置
- **图片修复用 API**：用户无 GPU 机器，AI 修复模型跑 CPU 太慢（30-60 秒/张），选用 Replicate 等托管 API（~$0.003/张）
- **UI / Worker 分离**：后台 asyncio task 独立于 Streamlit WebSocket 生命周期。浏览器关闭不影响 Worker 运行，重开页面通过 progress.json 轮询恢复进度
- **步骤级指数退避重试**：每个步骤独立重试（1s→3s→7s，最多 3 次），网络波动不导致永久失败，降级兜底后继续处理
- **断点续跑用 progress.json**：原子写入（.tmp → os.replace），防文件损坏。已完成的 ASIN 重启后跳过
- **进度轮询**：UI 端每 2 秒读 progress.json + st.rerun 刷新，而非 WebSocket 长连接
- **R2 公开访问**：使用 R2.dev subdomain 提供公开 URL，后续可绑定自定义域名
- **尺寸转换预留切换开关**：resize_mode 配置项（pad / outpainting），当前 pad 过渡，模型选型后升级

### 图片文件命名规则

```
images/{ASIN}/
├── 01_ru.jpg    # 有文字，已翻译
├── 02.jpg       # 无文字，仅改尺寸
├── 03_ru.jpg    # 有文字，已翻译
└── ...
```

- 序号对应亚马逊原图在 `图片url` 列中的位置（分号分隔后）
- `_ru` 后缀标记该图经过了翻译
- 本地存储和 R2 远端使用相同命名规则

### 容错策略详述

| 失败环节 | 处理 |
|---|---|
| 下载失败 | 保留原始 Amazon URL，报告标记 error |
| OCR 失败 | 跳过翻译，照样改尺寸 + 上传 R2（无文字），报告标记 skipped |
| 翻译失败 | 跳过文字覆写，照样改尺寸 + 上传 R2（含未擦除的英文），报告标记 error |
| AI 修复失败 | 跳过擦除+覆写，照样改尺寸 + 上传 R2，报告标记 error |
| R2 上传失败 | 本地文件已存，报告标记 error；输出 Excel 暂用本地路径 |

### 试跑预览交互

```
用户选 1 个 ASIN → 点「试跑预览」
→ 系统处理该 ASIN 全部图片
→ 每张图并排展示：原图（左）| 俄语图（右）
→ 用户可调整字体配置并刷新预览
→ 满意后点「开始全部处理」
```

## Testing Decisions

### 测试策略

只测试外部行为，不测试实现细节。通过依赖注入（`_xxx_override` 参数）在模块边界注入 mock。

### 测试层级（从高到低）

**最高接缝 — UI 逻辑层（image_translator_ui.py）**

遵循 `test_translator_ui.py` 的已有模式：mock 底层管道，测试编排逻辑。

- 文件上传校验：`validate_image_upload()` — 拒绝非 .xlsx、拒绝缺少必要列的 Excel、接受 4 列和 12 列两种格式、正确返回产品列表和 input_type
- 翻译编排：`execute_image_translation()` — mock 管道返回固定 ImageResult，验证 BatchImageResult 结构正确
- 输出生成：`generate_output_excel()` — 验证返回有效 xlsx 字节流，URL 已替换为 R2 URL
- 报告生成：`generate_report()` — 验证报告包含每张图的处理状态

**次高接缝 — 图片翻译管道（image_translator.py）**

- 正常流程：mock 所有外部依赖（下载/OCR/翻译/修复/上传），验证完整链路输出正确
- 单步骤失败：分别 mock 每步失败，验证兜底行为（仍改尺寸上传）
- ASIN 内并发：验证 asyncio.gather 正确聚合多图结果
- 断点续跑：验证 progress.json 读写、跳过已完成 ASIN

**最低接缝 — 图片编辑（image_processor.py）**

纯函数，最易测试：
- `resize_to_3x4(mode="pad")` — 输入任意分辨率 PIL Image，验证输出为 900×1200
- `overlay_text()` — 输入 PIL Image + TextRegion 列表，验证俄文写入位置正确
- FontConfig — 验证默认值和配置变更行为

**最低接缝 — R2 存储（r2_storage.py）**

Mock boto3 client：
- `upload()` — 验证调用 boto3.upload_file 参数正确，返回正确公开 URL
- `delete()` — 验证调用 boto3.delete_object 参数正确

### 新增测试：后台 Worker 与断网恢复（worker.py）

- Worker 生命周期：启动 → pause → resume → 完成
- progress.json 原子写入：验证不出现半截 JSON
- UI 进度轮询：mock progress.json 更新，验证 UI 状态刷新
- 会话恢复：页面加载时检测 progress.json state=running → 自动恢复进度显示
- 步骤级重试：mock 步骤前 2 次失败第 3 次成功，验证重试次数和退避间隔

### 已有测试模式参考

- `tests/test_translator_ui.py` — UI 逻辑层测试（mock provider 注入 `_provider_override`）
- `tests/test_crawler_ui.py` — UI 逻辑层测试（文件校验模式）
- `tests/test_excel_io.py` — Excel 读写测试（真实临时文件）
- `tests/test_translator.py` — 翻译模块单元测试

## Out of Scope

- AI 扩图（outpainting）模型的具体选型和接入——预留 resize_mode 配置开关，当前用 pad
- 自定义域名绑定到 R2——先用 `pub-xxx.r2.dev` 域名
- 图片批量重跑（仅处理失败图）——所有失败需要手动补，后续版本加此功能
- GPU 加速——当前 PaddleOCR 使用 CPU 模式
- 字体在线实时预览调参 Widget——当前通过修改配置项生效
- 图片翻译进度邮件/通知推送
- WebP/AVIF 等现代图片格式输出

## Further Notes

- R2 账号注册需要 VPN/代理访问 Cloudflare（中国大陆访问受限），用户尚未完成注册
- 字体默认使用 Roboto Regular（Google 开源，西里尔字母支持完整），存放于 `fonts/` 目录
- PaddleOCR 首次运行会自动下载模型（~200MB），需要网络环境
- 每张图预计 3-8 秒处理时间（取决于 API 响应和网络），500 ASIN × 6 图 ≈ 2.5-6.5 小时
- 断点续跑文件 `progress.json` 存储在项目根目录，每次 ASIN 完成后原子写入
