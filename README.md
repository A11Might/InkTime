# InkTime 可视化控制台

InkTime（[dai-hongtao/InkTime](https://github.com/dai-hongtao/InkTime)）照片分析管线的本地可视化调试台。
原项目：「可自托管的墨水屏电子相框，用 AI 分析照片库、按值得回忆度打分，并按“历史上的今天”
自动生成每日最具回忆价值的照片，让沉睡的记忆重新被看见。」
本控制台负责其中的「看」和「调」：浏览 `photos.db` 里的评分照片，实时预览 Quote/0
墨水屏（296×152，黑白 1-bit）渲染效果。不带 AI 选片逻辑。

![style](https://img.shields.io/badge/%E9%A3%8E%E6%A0%BC-%E7%BA%B8%E5%A2%A8%E5%B7%A5%E7%A8%8B%E9%A3%8E-2a78d6)

## 快速开始

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python mock/seed_mock.py        # 生成演示数据（30 张程序化场景图 + mock/photos.db）
python app.py                   # http://127.0.0.1:8788
```

## 接真实数据

复制 `config_example.py` 为 `config.py`，指向 analyze_photos.py 的产物：

```python
IMAGE_DIR = "/Users/you/Pictures/album"
DB_PATH = "/Users/you/InkTime/photos.db"
FONT_PATH = ""        # 留空自动找系统中文字体；推荐霞鹜文心宋体

# 推送到 Quote/0（可选，配好后「推送到设备」按钮生效）
DOT_API_KEY = ""      # Dot. App → 更多 → API Key → 创建
DOT_DEVICE_ID = ""    # 设备序列号
DOT_TASK_KEY = ""     # 可选：多个「图像 API」任务时指定
```

表结构与 `analyze_photos.py` 的 `photo_scores` 完全同构，零迁移。

## 推送到 Quote/0

用环境变量配置设备凭证（避免密钥写进文件）：

```bash
export DOT_API_KEY="dot_app_XXXX"    # Dot. App → 更多 → API Key → 创建（只展示一次）
export DOT_DEVICE_ID="ABCD1234"      # 设备序列号，App 设备详情里查看
python app.py
```

想长期生效就写进 `~/.zshrc`。也可写在 `config.py` 的同名变量里（环境变量优先）。
设备侧需满足：已接电源、已联网，且已在 App「内容工坊」把「图像 API」内容加入设备**循环**任务。

## 功能

- **画廊**：按分类筛选 / 回忆度·美观度·日期排序 / 文案与城市搜索
- **今日选片**：「历史上的今天」（按 EXIF 月-日匹配）的高分照片 Top 3
- **墨水屏预览**：右侧 Quote/0 设备拟真框，296×152 1-bit 渲染，切换照片时有墨水屏刷新动画
- **横竖构图自适应（实机验证）**：照片一律顶格铺满左侧、文字全在右侧白区，不裁主体、文字不压图——横图走「照片 203×152 等比顶满（永不裁剪）+ 右文字条（旁白每行 5 字，地点/日期在右下）」，竖图/方图走「照片 cover 116×152 顶满 + 右文字列（旁白每行 10 字，日期左 · 地点右）」（阈值 `SIDE_TEXT_MAX_ASPECT = 1.3` 可在 render.py 调）
- **按 iPhone 真实尺寸校准**：mock 数据覆盖 iPhone 全部拍摄档位——后置默认 24MP（5712×4284 / 4284×5712）、12MP（4032×3024 / 3024×4032，前置与长焦同此）、48MP 全开（8064×6048）、16:9 与 1:1 裁切，全部 4:3 系比例
- **屏上文案即时编辑**：改文案 / 地点 / 边框颜色 / 抖动即刻重渲染
- **抖动全对齐 Quote/0**：抖动类型（误差扩散 / 有序 / 关闭）× 10 种抖动算法（Floyd-Steinberg、Atkinson、Burkes、Sierra2、Stucki、Jarvis-Judice-Ninke、行/列/二维扩散、阈值）全部本地模拟，预览与设备显示一致
- **推送到设备**：一键把当前画面推到 Quote/0（官方图像 API）；每次推送生成独立页面（`/s/<id>`：原图 + 屏上效果对照），并自动作为碰一碰链接发给设备，手机 NFC 触碰即可打开；失败时按官方错误码给出中文排查提示
- **像素级一致**：推送图在本地按 Bayer 8×8 抖动成 1-bit（实机比选结论：小尺寸上比误差扩散干净稳定），以 `ditherType: NONE` 推送，设备侧不再二次处理；预览默认同为有序抖动，所见即所得（其余抖动算法仍可在预览中实验对比）

## 结构

```
app.py                 Flask：/api/stats /api/photos /api/thumb /api/render
render.py              Pillow 渲染器（实机验证版式：照片顶格铺左 → 自动对比度+锐化 → Bayer 1-bit → 右侧文字区）
config_example.py      配置模板（IMAGE_DIR / DB_PATH / FONT_PATH；推送凭证走环境变量）
mock/seed_mock.py      演示数据生成（程序化场景图 + 同构 SQLite）
static/ templates/     前端（无构建步骤，原生 HTML/CSS/JS）
```

## 设计

视觉语言取自 [dejev.app](https://dejev.app/zh-Hans)：暖纸面分层背景（`#f6f5f2`/`#fcfcfb`）、
近黑墨字（`#0b0b0b`）、单一蓝强调色（`#2a78d6`）、1px 暖灰描边 + 16px 圆角卡片、
等宽数字承载数据；深浅色跟随系统。
