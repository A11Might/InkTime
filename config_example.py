# 复制为 config.py 后按需修改。所有项都有兜底默认值，不改也能用 mock 数据跑起来。
import os

# 照片目录（analyze_photos.py 扫描的那个目录）
IMAGE_DIR = ""

# analyze_photos.py 生成的数据库；留空则自动用 mock/photos.db（本项目自带的演示数据）
DB_PATH = ""

# 渲染用中文字体（TTF/OTF/TTC）。留空则按候选列表自动找系统字体
FONT_PATH = ""

# 渲染画布（Quote/0: 2.66" 296x152, 125 PPI, 黑白）
CANVAS_W = 296
CANVAS_H = 152
# 版式常量（render.py 用）：照片顶格铺满左侧、文字全在右侧白区、不裁主体
WIDE_PHOTO_W = 203   # 横图：照片等比放进 203x152 顶满左侧（4:3 恰好铺满）
WIDE_TEXT_X = 208    # 横图：右文字条起点（宽 80px，旁白每行 5 字）
TALL_PHOTO_W = 116   # 竖图：照片 cover 裁剪 116x152 顶满左侧
TALL_TEXT_X = 126    # 竖图：右文字列起点（宽 162px，旁白每行 10 字）

# —— Dot. Quote/0 推送（可选；推荐用环境变量，这里留空兜底）——
# export DOT_API_KEY=...    # Dot. App → 更多 → API Key → 创建
# export DOT_DEVICE_ID=...  # 设备序列号，App 设备详情里查看
DOT_API_KEY = os.environ.get("DOT_API_KEY", "")
DOT_DEVICE_ID = os.environ.get("DOT_DEVICE_ID", "")
DOT_TASK_KEY = os.environ.get("DOT_TASK_KEY", "")   # 可选：多个「图像 API」任务时指定
DOT_API_BASE = os.environ.get("DOT_API_BASE", "https://dot.mindreset.tech")

