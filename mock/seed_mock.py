"""生成演示数据：mock/photos/*.jpg（程序化场景图，含横竖构图）+ mock/photos.db。

表结构与 InkTime analyze_photos.py 的真实 schema 一致，之后接真实数据零改动。
用法：python mock/seed_mock.py
"""
from __future__ import annotations

import random
import sqlite3
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).parent.parent
PHOTO_DIR = Path(__file__).parent / "photos"
DB_PATH = Path(__file__).parent / "photos.db"

W, H = 5712, 4284  # iPhone 16 Pro 后置默认 24MP 横版（4:3）
PW, PH = 4284, 5712  # 24MP 竖版
L12W, L12H = 4032, 3024  # 12MP 横版（前置 / 长焦）
P12W, P12H = 3024, 4032  # 12MP 竖版（前置自拍）
L48W, L48H = 8064, 6048  # 48MP 全开横版
W169W, W169H = 4032, 2268  # 16:9 裁切
SQW, SQH = 3024, 3024  # 方形裁切

SCHEMA = """
CREATE TABLE IF NOT EXISTS photo_scores (
    path TEXT PRIMARY KEY,
    caption TEXT, type TEXT,
    memory_score INTEGER, beauty_score INTEGER, reason TEXT,
    width INTEGER, height INTEGER, orientation TEXT,
    used_at TEXT, exif_json TEXT, raw_json TEXT,
    exif_datetime TEXT, exif_make TEXT, exif_model TEXT,
    exif_iso INTEGER, exif_exposure_time TEXT, exif_f_number REAL,
    exif_focal_length REAL, exif_gps_lat REAL, exif_gps_lon REAL, exif_gps_alt REAL,
    side_caption TEXT, exif_city TEXT
)
"""


# ---------- 程序化场景（w/h 任意） ----------

def _vgrad(img, top, bottom):
    for y in range(img.height):
        t = y / img.height
        c = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
        ImageDraw.Draw(img).line([(0, y), (img.width, y)], fill=c)


def _grain(img, amt=6):
    px = img.load()
    # 大图封顶迭代次数，24MP 也秒级完成
    for _ in range(min(img.width * img.height // 12, 150_000)):
        x, y = random.randrange(img.width), random.randrange(img.height)
        r, g, b = px[x, y][:3]
        d = random.randint(-amt, amt)
        px[x, y] = (max(0, min(255, r + d)), max(0, min(255, g + d)),
                    max(0, min(255, b + d)))


def _sun(draw, x, y, r, color):
    draw.ellipse([x - r, y - r, x + r, y + r], fill=color)


def scene_dawn_sea(seed, w, h):
    random.seed(seed)
    img = Image.new("RGB", (w, h))
    _vgrad(img, (250, 190, 150), (120, 150, 180))
    d = ImageDraw.Draw(img)
    horizon = h * 62 // 100
    d.rectangle([0, horizon, w, h], fill=(96, 118, 150))
    for y in range(horizon + 6, h, 14):
        seg = random.randint(w // 6, w // 3)
        x = random.randint(0, w - seg)
        d.line([(x, y), (x + seg, y)], fill=(230, 190, 160), width=2)
    _sun(d, w * 64 // 100, horizon - h * 8 // 100, w * 4 // 100 + 8, (255, 236, 210))
    for i in range(3):
        bx, by = w * 22 // 100 + i * 36, h * 26 // 100 + i * 14
        d.arc([bx, by, bx + 16, by + 10], 200, 340, fill=(60, 55, 70), width=2)
    _grain(img)
    return img


def scene_mountain_dusk(seed, w, h):
    random.seed(seed)
    img = Image.new("RGB", (w, h))
    _vgrad(img, (235, 160, 120), (90, 70, 110))
    d = ImageDraw.Draw(img)
    _sun(d, w * 30 // 100, h * 34 // 100, w * 3 // 100 + 8, (255, 220, 180))
    layers = [((120, 92, 128), 0.52), ((86, 66, 104), 0.66), ((52, 42, 70), 0.82)]
    for color, base in layers:
        pts = [(0, h)]
        y0 = h * base
        x = 0
        while x <= w:
            pts.append((x, y0 + random.randint(-h // 10, h // 13)))
            x += random.randint(w // 8, w // 5)
        pts.append((w, h))
        d.polygon(pts, fill=color)
    _grain(img, 5)
    return img


def scene_city_night(seed, w, h):
    random.seed(seed)
    img = Image.new("RGB", (w, h))
    _vgrad(img, (16, 20, 44), (46, 40, 78))
    d = ImageDraw.Draw(img)
    _sun(d, w * 76 // 100, h * 18 // 100, w * 3 // 100 + 4, (235, 235, 220))
    x = -10
    while x < w:
        bw = random.randint(w // 8, w // 4)
        bh = random.randint(h * 25 // 100, h * 62 // 100)
        d.rectangle([x, h - bh, x + bw, h], fill=(24, 26, 42))
        for wy in range(h - bh + 12, h - 10, 18):
            for wx in range(x + 8, x + bw - 8, 16):
                if random.random() < 0.42:
                    d.rectangle([wx, wy, wx + 6, wy + 9], fill=(255, 208, 120))
        x += bw + random.randint(4, 18)
    _grain(img, 4)
    return img


def scene_sakura(seed, w, h):
    random.seed(seed)
    img = Image.new("RGB", (w, h))
    _vgrad(img, (246, 214, 216), (222, 228, 232))
    d = ImageDraw.Draw(img)
    pts = [(w + 40, 40)]
    x, y = w + 40, 40
    for _ in range(9):
        x -= random.randint(w // 9, w // 6)
        y += random.randint(h // 22, h // 12)
        pts.append((x, y))
    d.line(pts, fill=(96, 72, 70), width=16, joint="curve")
    for i in range(4):
        sx, sy = pts[min(i + 2, len(pts) - 1)]
        d.line([(sx, sy), (sx - random.randint(w // 12, w // 7), sy + random.randint(h // 14, h // 8))],
               fill=(96, 72, 70), width=7)
    for _ in range(240):
        px, py = random.randint(0, w), random.randint(0, h)
        r = random.randint(2, 5)
        shade = random.choice([(247, 180, 195), (240, 158, 180), (252, 205, 215)])
        d.ellipse([px, py, px + r, py + r], fill=shade)
    _grain(img, 4)
    return img.filter(ImageFilter.SMOOTH_MORE)


def scene_forest(seed, w, h):
    random.seed(seed)
    img = Image.new("RGB", (w, h))
    _vgrad(img, (208, 220, 208), (120, 150, 122))
    d = ImageDraw.Draw(img)
    for i in range(5):
        y = h * (30 + i * 14) // 100
        d.rectangle([0, y, w, y + random.randint(10, 22)], fill=(226, 232, 224))
    for layer, (shade, n) in enumerate([((70, 100, 78), 9), ((48, 78, 58), 7), ((30, 52, 40), 5)]):
        for _ in range(n):
            tx = random.randint(-40, w)
            th = random.randint(h * 30 // 100, h * 55 // 100)
            tw = random.randint(w // 22, w // 12)
            base = h - layer * h * 5 // 100
            d.polygon([(tx, base - th), (tx - tw, base), (tx + tw, base)], fill=shade)
            d.rectangle([tx - 3, base, tx + 3, base + 26], fill=(40, 32, 26))
    _grain(img, 5)
    return img


def scene_desert_road(seed, w, h):
    random.seed(seed)
    img = Image.new("RGB", (w, h))
    _vgrad(img, (244, 200, 140), (210, 150, 96))
    d = ImageDraw.Draw(img)
    _sun(d, w * 70 // 100, h * 30 // 100, w * 5 // 100 + 8, (255, 244, 214))
    d.rectangle([0, h * 62 // 100, w, h], fill=(196, 138, 88))
    d.polygon([(w * 46 // 100, h * 62 // 100), (w * 54 // 100, h * 62 // 100),
               (w, h), (0, h)], fill=(90, 74, 66))
    for i in range(4):
        y0 = h * 62 // 100 + i * h * 8 // 100
        y1 = y0 + 26 + i * h // 30
        cx = w // 2
        spread = w // 60 + i * w // 34
        d.polygon([(cx - spread // 2, y0), (cx + spread // 2, y0),
                   (cx + spread, y1), (cx - spread, y1)], fill=(240, 226, 190))
    d.line([(0, h * 62 // 100), (w, h * 62 // 100)], fill=(150, 100, 64), width=3)
    _grain(img, 6)
    return img


SCENES = [scene_dawn_sea, scene_mountain_dusk, scene_city_night,
          scene_sakura, scene_forest, scene_desert_road]

# path, 场景, 分类, 文案, 城市, 纬度, 经度, 日期, 尺寸档位（iPhone 真实输出）
ROWS = [
    ("IMG_2041.jpg", 0, "旅行", "海把落日摊开成一片碎金。", "冰岛",   64.13, -21.90, "2024-09-20 21:14:05", "L48"),
    ("IMG_2042.jpg", 1, "风景", "群山把最后一束光收进口袋。", "稻城",   29.04, 100.30, "2023-09-20 19:48:33", "L24"),
    ("IMG_2043.jpg", 2, "城市", "十二点零四分，窗火还醒着七盏。", "上海", 31.23, 121.47, "2025-09-20 00:04:12", "L24"),
    ("IMG_2044.jpg", 3, "风景", "风一过，花瓣就替树走路。",   "京都",   35.01, 135.77, "2024-04-06 15:22:47", "P24"),
    ("IMG_2045.jpg", 4, "旅行", "雾走到哪，哪棵树就换一身衣裳。", "黑森林", 48.55, 8.20,  "2023-10-02 09:31:20", "L24"),
    ("IMG_2046.jpg", 5, "旅行", "这条路允许任何人往前走。",   "66号公路", 35.11, -111.67, "2022-09-20 17:05:59", "L24"),
    ("IMG_2047.jpg", 0, "生活", "早饭的粥比闹钟先热好。",     "杭州",   30.27, 120.16, "2025-06-14 07:12:40", "L24"),
    ("IMG_2048.jpg", 1, "风景", "今天的天色，适合把话留在心里。", "大理",   25.61, 100.27, "2025-08-30 18:44:10", "L24"),
    ("IMG_2049.jpg", 2, "城市", "天桥上有人哼歌，走音也理直气壮。", "重庆", 29.56, 106.55, "2024-11-21 22:18:03", "W16"),
    ("IMG_2050.jpg", 3, "生活", "院子里的猫先替我晒了太阳。",   "苏州",   31.30, 120.58, "2025-03-09 11:02:55", "SQ"),
    ("IMG_2051.jpg", 4, "人物", "她笑的时候，快门慢了半拍。",   "成都",   30.57, 104.07, "2024-05-18 14:26:31", "P12"),
    ("IMG_2052.jpg", 5, "美食", "汤要先喝，故事慢慢讲。",     "广州",   23.13, 113.26, "2025-01-11 19:40:22", "L24"),
    ("IMG_2053.jpg", 0, "宠物", "它占据沙发的方式是整个躺下。", "杭州",   30.27, 120.16, "2025-09-19 16:55:08", "L24"),
    ("IMG_2054.jpg", 1, "风景", "云层裂了一道缝，光准时赶到。", "拉萨",   29.65, 91.14,  "2023-07-23 12:36:44", "P24"),
    ("IMG_2055.jpg", 2, "旅行", "老街的石板记得每一双鞋。",   "绍兴",   30.03, 120.58, "2024-10-05 10:15:29", "L24"),
    ("IMG_2056.jpg", 3, "人物", "毕业那天，帽子和心事一起抛上天。", "武汉", 30.59, 114.31, "2025-06-28 11:48:16", "P12"),
    ("IMG_2057.jpg", 4, "生活", "阳台的植物又长高了一指。",   "南京",   32.06, 118.80, "2025-09-12 08:20:37", "L24"),
    ("IMG_2058.jpg", 5, "风景", "潮水退了，沙滩上全是脚印的诗。", "厦门", 24.48, 118.09, "2024-08-17 18:02:50", "L24"),
    ("IMG_2059.jpg", 0, "美食", "烤炉一开，整条巷子都饿了。",   "西安",   34.34, 108.94, "2025-02-16 18:30:11", "L24"),
    ("IMG_2060.jpg", 1, "旅行", "雪山不说话，但一直在回答。",   "丽江",   26.86, 100.23, "2023-12-30 13:55:42", "L24"),
    ("IMG_2061.jpg", 2, "城市", "晚高峰的车灯连成一条河。",   "深圳",   22.54, 114.06, "2025-09-20 18:58:26", "L24"),
    ("IMG_2062.jpg", 3, "宠物", "它盯着窗外，像在看自己的频道。", "北京", 39.90, 116.40, "2024-02-08 15:44:09", "SQ"),
    ("IMG_2063.jpg", 4, "风景", "湖面把整片晚霞原样抄了一遍。", "西宁",   36.62, 101.78, "2024-07-14 20:12:38", "W16"),
    ("IMG_2064.jpg", 5, "生活", "旧书摊上翻到一页没读完的话。", "天津",   39.13, 117.20, "2025-04-27 14:08:53", "L24"),
    ("IMG_2065.jpg", 0, "人物", "外婆的手艺，火候全在手上。",   "长沙",   28.23, 112.94, "2024-01-29 17:22:18", "L24"),
    ("IMG_2066.jpg", 1, "旅行", "灯塔守着夜，船守着灯塔。",     "青岛",   36.07, 120.38, "2023-05-06 21:36:57", "L24"),
    ("IMG_2067.jpg", 2, "风景", "稻田黄得很守时，一年一次。",   "婺源",   29.25, 117.86, "2025-09-20 16:40:05", "P24"),
    ("IMG_2068.jpg", 3, "生活", "修好了台灯，也顺手修好了心情。", "合肥", 31.82, 117.23, "2024-09-08 20:31:44", "L24"),
    ("IMG_2069.jpg", 4, "城市", "图书馆的灯，亮到最后一个走的人。", "上海", 31.23, 121.47, "2025-05-02 21:14:36", "L24"),
    ("IMG_2070.jpg", 5, "旅行", "背包比来时重，是装了风景。",   "贵阳",   26.65, 106.63, "2024-06-19 12:58:21", "P24"),
]

TYPES_WEIGHT = {"旅行": 88, "风景": 82, "人物": 90, "宠物": 84, "城市": 72,
                "生活": 68, "美食": 60}

SIZES = {
    "L24": (W, H),        # 后置默认 24MP 横
    "P24": (PW, PH),      # 后置默认 24MP 竖
    "L12": (L12W, L12H),  # 12MP 横（前置 / 长焦）
    "P12": (P12W, P12H),  # 12MP 竖（前置自拍）
    "L48": (L48W, L48H),  # 48MP 全开横
    "W16": (W169W, W169H),  # 16:9 裁切
    "SQ": (SQW, SQH),     # 1:1 方形
}


def build_scores(path: str, scene_idx: int, typ: str) -> tuple[int, int]:
    """返回 (memory, beauty)。让分数和分类、场景有相关性，数据看起来可信。"""
    rng = random.Random(path)
    base = TYPES_WEIGHT.get(typ, 70)
    memory = max(8, min(99, base + rng.randint(-18, 14)))
    beauty = max(15, min(99, 62 + scene_idx * 4 + rng.randint(-20, 22)))
    return memory, beauty


def main():
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    conn.execute(SCHEMA)
    for i, (name, scene_idx, typ, caption, city, lat, lon, dt, size) in enumerate(ROWS):
        sw, sh = SIZES[size]
        img = SCENES[scene_idx](i * 131 + 7, sw, sh)
        img.save(PHOTO_DIR / name, "JPEG", quality=88)
        memory, beauty = build_scores(name, scene_idx, typ)
        conn.execute(
            "INSERT OR REPLACE INTO photo_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(PHOTO_DIR / name), caption, typ, memory, beauty,
                "mock", img.width, img.height, "P" if sh > sw else "L",
                None, None, None, dt, "Apple", "iPhone 15 Pro",
                125, "1/120", 1.8, 24.0, lat, lon, 12.0, caption, city,
            ),
        )
    conn.commit()
    conn.close()
    n_p = sum(1 for r in ROWS if SIZES[r[8]][1] > SIZES[r[8]][0])
    print(f"seeded {len(ROWS)} photos ({n_p} 竖版) -> {PHOTO_DIR}")
    print(f"db -> {DB_PATH}")


if __name__ == "__main__":
    main()
