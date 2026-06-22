# 图片翻译功能 — TDD 分段实施文档

基于 [PRD-image-translation.md](./PRD-image-translation.md) 和 [#6 图片翻译全链路](./006-图片翻译全链路.md) 拆分。

## 架构概览：UI / Worker 分离

```
┌─ Streamlit UI（浏览器）─────┐        ┌─ 后台 Worker（asyncio task）─┐
│                              │        │                              │
│  文件上传 + 校验             │        │  WorkerManager（单例）        │
│  试跑预览（原图vs俄语图）    │  触发  │  ├─ start(products, config)   │
│  进度轮询 + 实时刷新         │ ────→  │  ├─ pause()                  │
│  下载 Excel + 报告          │  读    │  ├─ resume()                 │
│                              │ ←────  │  └─ get_status()             │
│  开启 / 暂停 / 恢复         │  写    │                              │
│                              │        │  translate_batch()           │
│  关闭浏览器 → Worker 继续跑  │        │  ├─ 每完成 1 ASIN → 写      │
│  重开浏览器 → 自动检测恢复   │        │  │  progress.json            │
│                              │        │  ├─ 步骤级重试 ×3           │
│                              │        │  └─ 网络断开 → 等待恢复     │
└──────────────────────────────┘        └──────────────────────────────┘
```

**核心行为：**
- 用户点「开始」→ Worker 在独立 asyncio task 启动，**不绑定 WebSocket 生命周期**
- Worker 每完成一个 ASIN → 写 `progress.json`（原子写入，防损坏）
- UI 用 `st.rerun` + 定时轮询 `progress.json`，刷新进度条和日志
- **关闭浏览器页 → Worker 继续跑**；重开页面 → 检测进行中任务 → 恢复 UI 状态
- 网络断开 → Worker 步骤级重试（指数退避 1s→3s→7s，最多 3 次）→ 仍失败则降级兜底 → 继续下一个 ASIN
- **不需要人工恢复**：Worker 一直在后台，网络恢复后下一个 ASIN 或重试自然恢复

---

## 实施顺序与依赖

```
#7 基础设施 ──→ #8 编辑引擎 ──→ #9 单图管道+后台Worker ──→ #10 UI集成+进度轮询 ──→ #11 端到端验证(HITL)
```

---

## #7 — 图片翻译基础设施

**类型**：AFK | **阻塞于**：无 | **覆盖用户故事**：#15, #24, #25, #26, #27

### 测什么（先写测试）

| 测试 | 验证行为 |
|---|---|
| `test_r2_upload_returns_public_url` | mock boto3，验证 upload 调用了 `s3.upload_file`，参数正确，返回公开 URL |
| `test_r2_delete_calls_delete_object` | mock boto3，验证 delete 调用了 `s3.delete_object` |
| `test_r2_config_from_env` | 从 .env 读取 R2_ACCESS_KEY_ID 等 5 个配置项，验证 Settings 对象属性值正确 |
| `test_image_translation_prompt_loaded` | 验证 `prompts/image_translation_persona.txt` 文件存在且非空，含核心规则关键词 |
| `test_image_config_defaults` | IMAGE_LOCAL_PATH 默认 "images"，IMAGE_RESIZE_MODE 默认 "pad"，IMAGE_OUTPUT_SIZE 默认 "900x1200" |
| `test_retry_config_defaults` | STEP_MAX_RETRIES 默认 3，RETRY_BACKOFF_BASE 默认 2，STEP_TIMEOUT 默认 60 |
| `test_progress_json_atomic_write` | 模拟写 progress.json 中途断电，验证文件不会损坏（不存在部分写入的 JSON） |

### 写什么

1. **config.py**（修改）— 新增配置项：
   ```env
   # Cloudflare R2
   R2_ACCESS_KEY_ID=
   R2_SECRET_ACCESS_KEY=
   R2_ACCOUNT_ID=
   R2_BUCKET=wb-product-images
   R2_PUBLIC_DOMAIN=

   # 图片处理
   IMAGE_LOCAL_PATH=images
   IMAGE_RESIZE_MODE=pad
   IMAGE_OUTPUT_SIZE=900x1200

   # 重试与超时
   STEP_MAX_RETRIES=3
   STEP_TIMEOUT=60
   RETRY_BACKOFF_BASE=2

   # AI 修复 API
   IMAGE_REPAIR_PROVIDER=replicate
   IMAGE_REPAIR_API_KEY=
   IMAGE_REPAIR_MODEL=tencentarc/gfpgan
   ```

2. **r2_storage.py**（新建）— R2Storage 类：
   ```python
   class R2Storage:
       def __init__(self, config): ...
       def upload(self, local_path: str, remote_key: str) -> str: ...  # 返回公开 URL
       def delete(self, remote_key: str) -> None: ...
   ```
   boto3 S3 兼容协议，endpoint_url 用 account_id 拼接。

3. **prompts/image_translation_persona.txt**（新建）：
   ```
   # 角色
   你是电商图片文字翻译专家，精通英俄双语。

   ## 翻译规则
   1. 精准直译，不增删信息
   2. 俄语长度 ≤ 原文 1.3 倍
   3. 技术参数/数字/单位精确保留
   4. 不进行 SEO 改写
   5. 全小写，无特殊符号

   ## 输出格式
   {"translations": ["译文1", "译文2", ...]}
   ```

4. **requirements.txt**（修改）— 新增：`paddleocr>=2.8`, `paddlepaddle>=3.0`, `boto3>=1.35`, `Pillow>=10.0`, `replicate>=1.0`

### 验收标准

- [ ] `from r2_storage import R2Storage` 成功
- [ ] `.env.template` 包含新增的 8 个配置项
- [ ] `prompts/image_translation_persona.txt` 文件存在

---

## #8 — 图片编辑引擎

**类型**：AFK | **阻塞于**：#7 | **覆盖用户故事**：#6, #9, #10, #11, #12, #13, #14

### 测什么（先写测试）

| 测试 | 验证行为 |
|---|---|
| `test_resize_pad_square_to_3x4` | 输入 1500×1500 PIL Image，输出 900×1200，内容等比缩放居中，上下白边，**内容无变形无拉伸** |
| `test_resize_pad_wide_to_3x4` | 输入 2000×1500，输出 900×1200，等比缩放，左右被裁，**内容无拉伸** |
| `test_resize_pad_tall_to_3x4` | 输入 1000×2000，输出 900×1200，等比缩放，上下被裁，左右白边，**内容无拉伸** |
| `test_resize_no_stretch` | 输入任意尺寸，验证缩放后图片中心区域像素与原始图片相同（等比缩放，无变形） |
| `test_font_config_defaults` | FontConfig 默认 font_name="Roboto-Regular.ttf", auto_size=True, color_mode="inherit" |
| `test_font_config_auto_size_calculation` | 给一个 200×50 的文字区域，auto_size=True 时计算出合理字号 |
| `test_overlay_text_writes_russian` | 给一张图 + [TextRegion("Hello", "привет", box)]，验证图片像素变化发生在 box 区域内 |
| `test_overlay_text_empty_regions_noop` | 传入空 TextRegion 列表，图片像素完全不变 |

### 写什么

1. **image_processor.py**（新建）：

```python
@dataclass
class FontConfig:
    font_name: str = "Roboto-Regular.ttf"
    auto_size: bool = True
    manual_size: int = 24
    color_mode: str = "inherit"  # "inherit" | "auto" | "fixed"
    fixed_color: str = "#000000"

@dataclass
class TextRegion:
    text: str              # OCR 原文
    translation: str       # DeepSeek 译文
    box: tuple             # PaddleOCR 四角坐标

def resize_to_3x4(image: Image, target_size=(900, 1200), mode="pad") -> Image:
    """
    等比缩放 + 居中填充到 3:4 画布。

    原则：绝不拉伸、不变形。原图等比缩放至 target_size 内最大适配尺寸，
    居中放置，空白区域白色填充。宽图（>3:4）左右被裁，窄图（<3:4）上下白边。
    """

def erase_text_regions(image: Image, regions: list[TextRegion], repair_api_key: str) -> Image:
    """对每个 region.box 调用 Replicate Lama Cleaner API 做背景修复擦除。"""

def overlay_russian_text(image: Image, regions: list[TextRegion], font: FontConfig) -> Image:
    """对每个 region 在 box 区域内写入 translation 俄文，字号/颜色按 FontConfig。"""
```

### 验收标准

- [ ] 一张白底 1500×1500 产品图 → resize → 输出 900×1200，PNG 保存后确认分辨率正确
- [ ] 一张有英文文字的产品图 → 手动构造 TextRegion → overlay → 俄文写入在正确位置

---

## #9 — 单图翻译管道 + 后台 Worker

**类型**：AFK | **阻塞于**：#7, #8 | **覆盖用户故事**：#5, #7, #8, #19, #20, #21, #22, #23 + 后台运行 + 断网续跑

### 测什么（先写测试）

| 测试 | 验证行为 |
|---|---|
| `test_single_image_full_pipeline` | mock 所有外部调用（下载/OCR/翻译/修复/上传），验证 ImageResult 各字段正确 |
| `test_single_image_no_text_skips_translation` | mock OCR 返回空列表，验证跳过翻译+擦除+覆写，但仍改尺寸+上传 |
| `test_single_image_download_fails` | mock download 抛异常，验证 ImageResult.error 非空，r2_url 保留原始 URL |
| `test_single_image_ocr_fails` | mock OCR 抛异常，验证 skip 翻译但照样 resize+upload |
| `test_single_image_translate_fails` | mock DeepSeek 失败，验证 skip 覆写但照样 resize+upload（保留英文） |
| `test_single_image_repair_fails` | mock Lama Cleaner 失败，验证 skip 擦除+覆写但照样 resize+upload |
| `test_single_image_r2_upload_fails` | mock R2 upload 抛异常，验证本地文件仍保存，ImageResult 用本地路径 |
| `test_step_retry_with_exponential_backoff` | mock download 前两次抛 Timeout，第三次成功，验证重试了 3 次且间隔递增 |
| `test_step_exhausts_retries_then_falls_back` | mock download 连续 3 次抛异常，验证最终走降级逻辑（标记 error） |
| `test_asin_images_concurrent` | 一个 ASIN 有 3 张图，验证 3 张图并发处理，总耗时 < 3×单张耗时（非精确） |
| `test_batch_resume_skips_completed_asins` | progress.json 已有 2 个完成 ASIN，输入含 4 个 ASIN，验证只处理未完成的 2 个 |
| `test_batch_writes_progress_after_each_asin` | mock 处理 2 个 ASIN，验证 progress.json 在第一个 ASIN 完成后写入 |
| `test_worker_runs_independent_of_ui` | 启动 Worker 后模拟"关闭页面"（不调用 cancel），验证 Worker 继续运行并写入 progress.json |
| `test_worker_status_reflects_state` | WorkerManager.get_status() 返回 running/paused/completed/idle 状态正确 |
| `test_progress_json_atomic_write_no_corruption` | 在写 progress.json 时强制 kill 进程，验证文件不存在部分内容 |

### 写什么

1. **image_translator.py**（新建）：

```python
@dataclass
class ImageResult:
    index: int
    original_url: str
    r2_url: str                    # 翻译后 R2 URL（失败回退原始 URL）
    local_path: str
    has_text: bool
    translated: bool
    status: str                    # "ok" | "skipped" | "error"
    error: str
    retry_count: int               # 该图总重试次数
    ocr_original_texts: list[str]
    translated_texts: list[str]

@dataclass
class AsinImageResult:
    asin: str
    images: list[ImageResult]
    success_count: int
    error_count: int
    skipped_count: int

@dataclass
class BatchImageResult:
    results: list[AsinImageResult]
    total_asins: int
    completed_asins: int
    total_images: int
    success_images: int
    error_images: int
    skipped_images: int
    started_at: str
    finished_at: str

async def translate_single_image(
    image_url: str, asin: str, index: int,
    font_config: FontConfig,
    *,
    _download_func=None, _ocr_func=None, _translate_func=None,
    _repair_func=None, _resize_func=None, _upload_func=None,
) -> ImageResult: ...

async def translate_asin_images(
    product: dict, font_config: FontConfig,
) -> AsinImageResult: ...

async def translate_batch(
    products: list[dict],
    font_config: FontConfig = FontConfig(),
    progress_callback: Callable | None = None,
    resume_from: str | None = None,
) -> BatchImageResult: ...
```

2. **worker.py**（新建）— WorkerManager 单例：

```python
import json, os, time, asyncio

PROGRESS_FILE = "progress.json"

@dataclass
class WorkerStatus:
    state: str         # "idle" | "running" | "paused" | "completed" | "error"
    total_asins: int
    completed_asins: int
    total_images: int
    processed_images: int
    current_asin: str
    started_at: str
    updated_at: str
    error: str

class WorkerManager:
    """
    UI/Worker 分离的核心桥梁。

    - start(): 启动后台 asyncio task（不依赖 Streamlit WebSocket）
    - get_status(): 读 progress.json 返回 WorkerStatus
    - pause()/resume(): 控制任务暂停/恢复
    - 线程安全：progress.json 原子写入（先写 tmp 再 os.replace）
    """

    _instance = None

    def __init__(self): ...
    def start(self, products, font_config) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def get_status(self) -> WorkerStatus: ...

    @staticmethod
    def _atomic_write_progress(data: dict) -> None:
        """写 tmp 文件 → os.replace → 保证不出现半截 JSON"""
        tmp = PROGRESS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, PROGRESS_FILE)  # 原子操作
```

### 步骤级重试机制

每个步骤（下载/OCR/翻译/修复/上传）独立包装：

```python
async def _retry_step(step_name: str, fn, max_retries=3, backoff_base=2):
    """
    指数退避重试: 1s → 3s → 7s
    总超时 = 1+3+7 = 11s，远小于 60s 的步骤超时
    """
    for attempt in range(max_retries):
        try:
            return await asyncio.wait_for(fn(), timeout=60)
        except asyncio.TimeoutError:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(backoff_base ** attempt)
        except Exception:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(backoff_base ** attempt)
```

**网络断连场景**：下载/上传步骤 timeout 60s → 重试 3 次（1s, 3s, 7s）→ 仍失败 → 降级兜底 → Worker 继续下一个 ASIN。网络恢复后下一个 ASIN 自动恢复。

### 容错降级链（不变）

```
下载失败 → 保留原始URL，标记error，终止该图后续步骤
OCR失败   → 跳过翻译+擦除+覆写，继续 resize+upload，标记skipped
翻译失败  → 跳过文字覆写（英文未擦），继续 resize+upload，标记error
修复失败  → 跳过擦除+覆写，继续 resize+upload，标记error
上传失败  → 本地已存，ImageResult.r2_url 回退本地路径，标记error
```

### 断点续跑 progress.json 格式

```json
{
  "state": "running",
  "completed_asins": ["B0GVYXC124", "B0F45N6NS7"],
  "current_asin": "B0XXXXXXX",
  "total_asins": 50,
  "total_images": 300,
  "processed_images": 120,
  "started_at": "2026-06-15T14:30:00",
  "updated_at": "2026-06-15T14:45:30"
}
```

### 验收标准

- [ ] 用一个真实 ASIN 的 3 张图跑完整管道，每张都在 3-8 秒内完成
- [ ] 无文字图只 resize+upload，不触发 API 调用
- [ ] mock 步骤连续失败 3 次 → 走降级逻辑（不崩溃）
- [ ] 重试间隔验证：1s → 3s → 7s
- [ ] progress.json 正确读写，中断后重启跳过已完成 ASIN
- [ ] 启动 Worker 后关闭 Streamlit 页面 → Worker 继续运行 → progress.json 持续更新

---

## #10 — 图片翻译 UI 集成 + 后台进度轮询

**类型**：AFK | **阻塞于**：#9 | **覆盖用户故事**：#1, #2, #3, #4, #16, #17, #18, #27 + 后台运行 + 会话恢复

### 测什么（先写测试）

| 测试 | 验证行为 |
|---|---|
| `test_validate_4col_excel` | 上传爬虫 4 列 Excel，返回 input_type="crawler_output"，产品列表正确 |
| `test_validate_12col_excel` | 上传翻译后 12 列 Excel，返回 input_type="translation_output"，产品列表正确 |
| `test_validate_rejects_missing_image_url_column` | 缺少 图片url 列，返回 is_valid=False + 错误信息 |
| `test_validate_rejects_non_xlsx` | .csv 文件，返回 is_valid=False |
| `test_execute_image_translation_returns_batch_result` | mock pipeline，验证返回 BatchImageResult 结构正确 |
| `test_generate_output_excel_urls_replaced` | 验证输出 xlsx 中 图片url 列全部是 R2 URL（非原始 Amazon URL） |
| `test_generate_report_contains_all_statuses` | 报告含 ok/skipped/error 三类图片，验证每行列出了状态和 asin |
| `test_ui_detects_running_worker_on_page_load` | progress.json state=running，验证页面正确恢复进度显示（不重复启动） |
| `test_ui_polling_updates_progress` | mock Worker 更新 progress.json，验证 UI 轮询后进度值刷新 |
| `test_pause_and_resume_worker` | 点暂停 → WorkerStatus.state=paused → 点恢复 → state 回到 running |

### 写什么

1. **image_translator_ui.py**（新建）：

```python
@dataclass
class ImageUploadResult:
    is_valid: bool
    error: str
    products: list[dict]
    count: int
    input_type: str  # "crawler_output" | "translation_output"

def validate_image_upload(filepath: str) -> ImageUploadResult: ...

def start_background_translation(
    products: list[dict],
    font_config: FontConfig,
) -> None:
    """启动后台 Worker（不阻塞），WorkerManager.start() 立即返回。"""

def get_worker_status() -> WorkerStatus:
    """读取 progress.json 返回当前 Worker 状态。"""

def generate_output_excel(results: BatchImageResult) -> bytes: ...
def generate_report(results: BatchImageResult) -> bytes: ...
```

2. **app.py**（修改）— 新增 Tab 3 + 后台轮询：

```
┌───────────────────────────────────────────────────────┐
│  [爬虫采集]  [文案翻译]  [🖼️ 图片翻译]                │
├───────────────────────────────────────────────────────┤
│                                                       │
│  ── 上传 ──                                          │
│  上传 Excel（兼容爬虫 4 列或翻译后 12 列）            │
│  ┌──────────────────────────────────────────┐        │
│  │  拖拽或点击上传 .xlsx 文件                │        │
│  └──────────────────────────────────────────┘        │
│  已识别：3 个 ASIN，共 18 张图片                      │
│                                                       │
│  ── 字体配置 ──                                      │
│  字体: [Roboto-Regular.ttf ▼]                        │
│  字号: [自动适配 ▼]                                  │
│  颜色: [继承原文 ▼]                                  │
│                                                       │
│  ┌── 试跑预览 ──┐   ┌── 开始全部处理 ──┐            │
│  选择一个 ASIN: [B0GVYXC124 ▼]                       │
│                                                       │
│  ┌──────────────┐  ┌──────────────┐                  │
│  │  原图        │  │  俄语图      │                  │
│  │  (1500×1500) │  │  (900×1200)  │                  │
│  └──────────────┘  └──────────────┘                  │
│  ...（每张图并排对比）...                             │
│                                                       │
│  ── 处理进度 ──                                      │
│  ████████████░░░░░░░░  2/3 ASIN  (后台运行中 🟢)    │
│  📊 图片: 18/18  |  成功: 15  |  跳过: 2  |  失败: 1 │
│  ...（实时日志流，最新 5 条）...                      │
│                                                       │
│  ┌ 暂停 ┐  ┌ 下载处理后 Excel ┐  ┌ 下载处理报告 ┐  │
│                                                       │
│  ⚠️  关闭本页面后，后台继续处理。                       │
│     重开页面自动恢复进度。                            │
└───────────────────────────────────────────────────────┘
```

**侧边栏新增：**
- R2 配置（5 个字段）
- 图片 resize_mode（下拉选择）
- AI 修复 API Key
- 重试配置（最大重试次数、超时秒数）

### 进度轮询机制

```python
# app.py 中的轮询逻辑
import time

if "worker_started" not in st.session_state:
    st.session_state.worker_started = False

if st.session_state.worker_started:
    # 每 2 秒从 progress.json 读最新进度
    status = get_worker_status()

    # 进度条
    st.progress(status.completed_asins / status.total_asins)
    st.caption(f"后台运行中 🟢 | {status.completed_asins}/{status.total_asins} ASIN | "
               f"{status.processed_images}/{status.total_images} 图片")

    # 2 秒后自动刷新
    time.sleep(2)
    st.rerun()
```

### 会话恢复

```
页面加载
  ↓
检测 progress.json 是否存在
  ↓
存在 + state="running" → 自动恢复进度UI，显示 "后台运行中 🟢"
存在 + state="completed" → 显示结果汇总 + 下载按钮
不存在 → 初始状态，等待上传文件
```

### 试跑预览流程

1. 用户从已识别的 ASIN 列表中选 1 个
2. 点「试跑预览」
3. 系统只处理该 ASIN 的全部图片（同步等待，不启动后台 Worker）
4. 每张图并排展示：原图（左）| 俄语图（右）
5. 用户调整字体配置 → 刷新重试
6. 满意后点「开始全部处理」→ 启动后台 Worker

### 验收标准

- [ ] Tab 3 正常显示，三个 Tab 切换正常
- [ ] 上传 4 列 Excel → 正确识别 ASIN 和图片数
- [ ] 上传 12 列 Excel → 正确识别
- [ ] 试跑 1 个 ASIN → 并排对比图显示
- [ ] 全量处理启动后，关闭浏览器页面 → 等待 30s 重开 → 进度自动恢复
- [ ] 进度条实时更新（每 2 秒刷新）
- [ ] 暂停/恢复按钮可用
- [ ] 下载 Excel → 图片 URL 全部替换为 R2 URL
- [ ] 下载报告 → 含每张图的状态和错误信息

---

## #11 — R2 注册 + 端到端验证（HITL）

**类型**：HITL | **阻塞于**：#10 | **覆盖用户故事**：#15, #16

### 用户操作步骤

1. 开 VPN → 访问 https://dash.cloudflare.com/sign-up 注册
2. 左侧 R2 → Create Bucket（名称随意，如 `wb-product-images`）
3. 右侧面板找 Account ID（32 位 hex），记入 .env → `R2_ACCOUNT_ID`
4. Manage R2 API Tokens → Create → Object Read & Write → 复制 Access Key ID + Secret → 记入 .env
5. Bucket Settings → Public Access → 开 R2.dev subdomain → 记域名 → `.env` → `R2_PUBLIC_DOMAIN`

### 验收标准

- [ ] R2 上传测试图后，浏览器打开公开 URL 能显示图片
- [ ] 端到端：上传 Excel → Tab3 跑 3 个 ASIN → Excel 中 R2 URL 全可公网打开
- [ ] 本地 `images/{ASIN}/` 目录结构正确，文件命名规则正确

---

## 完整依赖关系图

```
#7 (基础设施) ──┬──→ #8 (编辑引擎) ──→ #9 (单图管道) ──→ #10 (UI集成) ──→ #11 (HITL)
               │
               └──→ (直接依赖，用于存储+翻译)
```

