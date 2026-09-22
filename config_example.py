# 复制为 config.py 后按需修改。所有项都有兜底默认值，不改也能用 mock 数据跑起来。

# 照片目录（analyze_photos.py 扫描的那个目录）
IMAGE_DIR = ""

# analyze_photos.py 生成的数据库；留空则自动用 mock/photos.db（本项目自带的演示数据）
DB_PATH = ""

# 渲染用中文字体（TTF/OTF/TTC）。留空则按候选列表自动找系统字体
FONT_PATH = ""

# —— analyze_photos.py 照片分析（VLM）——
# 渠道列表按优先级排列，某渠道失败/429 时自动切到下一个。
# LM Studio：加载视觉模型（推荐 mlx-community/Qwen3.5-9B-6bit）后，
# 在 Developer 页启动本地服务；model_name 以 LM Studio 里实际加载的 id 为准。
# 商用 API：api_url 换成 OpenAI 兼容端点并填 api_key，model_name 用视觉多模态模型（如 glm-4.5v）。
API_CHANNELS = [
    {
        "api_url": "http://127.0.0.1:1234/v1/chat/completions",
        "api_key": "",
        "model_name": "qwen3.5-9b",
    },
]

# 每次最多处理多少张（None = 不限）；单次请求超时（秒）
BATCH_LIMIT = None
TIMEOUT = 600

# 发 VLM 前把图片长边缩到该值（本地推理可保持 2560；云端建议 1280 省图片 token）
VLM_MAX_LONG_EDGE = 2560

# 离线中文城市索引（GPS → 城市名），相对路径基于仓库根
WORLD_CITIES_CSV = "./data/world_cities_zh.csv"
CITY_MAX_DISTANCE_KM = 80.0

# 常驻坐标：照片 GPS 离这里超过 HOME_RADIUS_KM 视为「异地」，评分小幅加成
HOME_LAT = 31.8252
HOME_LON = 117.2273
HOME_RADIUS_KM = 60.0

# 渲染画布（Quote/0: 2.66" 296x152, 125 PPI, 黑白）
CANVAS_W = 296
CANVAS_H = 152
# 版式常量（render.py 用）：照片顶格铺满左侧、文字全在右侧白区、不裁主体
WIDE_PHOTO_W = 203   # 横图：照片等比放进 203x152 顶满左侧（4:3 恰好铺满）
WIDE_TEXT_X = 208    # 横图：右文字条起点（宽 80px，旁白每行 5 字）
TALL_PHOTO_W = 116   # 竖图：照片 cover 裁剪 116x152 顶满左侧
TALL_TEXT_X = 126    # 竖图：右文字列起点（宽 162px，旁白每行 10 字）

# —— Dot. Quote/0 推送凭证 ——
# API Key：Dot. App → 更多 → API Key → 创建；设备序列号在 App 设备详情里查看
DOT_API_KEY = ""
DOT_DEVICE_ID = ""
DOT_TASK_KEY = ""   # 可选：多个「图像 API」任务时指定（值从「列出设备任务」接口拿）
DOT_TASK_ALIAS = "" # 可选：给这个内容起个人看得懂的名字，会显示在 Dot. App 的任务列表里；不填保持原名
DOT_API_BASE = "https://dot.mindreset.tech"

# —— 每日自动推送（定时器内置在 app.py 服务里；daily_push.py 是选片逻辑 + 手动入口）——
# 选片：只在「历史上的今天」（月-日相同）里挑回忆分达到该值的照片，优先没推送过的；
# 今天没有合适的就往前一天天回退，最多一年；再没有就用全库最高分兜底
MEMORY_THRESHOLD = 70
# 每天推几张（一块 Quote/0 屏建议 1）
DAILY_COUNT = 1
# 控制台服务（app.py）常驻时，每天到点自动选片推送；不想要设 False
AUTO_PUSH = True
PUSH_HOUR = 8    # 推送时刻（24 小时制）
PUSH_MINUTE = 0

