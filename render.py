"""296x152 黑白墨水屏渲染器（Quote/0 规格）。

按宽高比自适应版式：横图走「通栏照片 + 底部文字条」，竖图/方图走「照片靠左全高 + 右侧文字栏」。
抖动算法与 Quote/0 图像 API 的 ditherType/ditherKernel 一致，本地模拟保证预览即所得：
- DIFFUSION（误差扩散）：FLOYD_STEINBERG / ATKINSON / BURKES / SIERRA2 / STUCKI /
  JARVIS_JUDICE_NINKE / DIFFUSION_ROW / DIFFUSION_COLUMN / DIFFUSION_2D
- ORDERED（有序抖动）：8x8 Bayer 矩阵
- NONE：不抖动（预览按 128 阈值模拟 1-bit 屏效果）

render_photo()   → 1-bit 预览图（含边框模拟）
render_push_png()→ 未抖动灰度 PNG（推送给设备，由设备按参数抖动、画边框）
"""
from __future__ import annotations

import io
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

sys.path.insert(0, str(Path(__file__).parent))
try:
    import config
except ImportError:
    import config_example as config  # type: ignore

CANVAS_W = getattr(config, "CANVAS_W", 296)
CANVAS_H = getattr(config, "CANVAS_H", 152)
PHOTO_H = getattr(config, "PHOTO_H", 116)
TEXT_H = CANVAS_H - PHOTO_H

# 宽高比低于此值的照片（竖图/方图）走「照片靠左 + 右侧文字栏」版式，
# 避免被横条铺满裁剪裁掉主体。
# iPhone 照片全是 4:3（横 1.333 / 竖 0.75）：阈值取 1.3，
# 4:3 横图仍走通栏，竖图和方图走侧排。
SIDE_TEXT_MAX_ASPECT = 1.3
PHOTO_BOX_MAX_W = 140  # 侧排版式照片盒最大宽，超出则轻微裁边

INK = 0
PAPER = 255

# ---------- 抖动算法定义（与 Quote/0 ditherKernel 对齐） ----------

# (dx, dy, weight) 相对当前像素，weight 除以 divisor
_ERROR_KERNELS: dict[str, tuple[list[tuple[int, int, int]], int]] = {
    "FLOYD_STEINBERG": ([(1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1)], 16),
    "ATKINSON": ([(1, 0, 1), (2, 0, 1), (-1, 1, 1), (0, 1, 1), (1, 1, 1), (0, 2, 1)], 8),
    "BURKES": ([(1, 0, 8), (2, 0, 4), (-2, 1, 2), (-1, 1, 4), (0, 1, 8), (1, 1, 4), (2, 1, 2)], 32),
    "SIERRA2": ([(1, 0, 4), (2, 0, 3), (-2, 1, 1), (-1, 1, 2), (0, 1, 3), (1, 1, 2), (2, 1, 1)], 16),
    "STUCKI": ([(1, 0, 8), (2, 0, 4), (-2, 1, 2), (-1, 1, 4), (0, 1, 8), (1, 1, 4), (2, 1, 2),
                (-2, 2, 1), (-1, 2, 2), (0, 2, 4), (1, 2, 2), (2, 2, 1)], 42),
    "JARVIS_JUDICE_NINKE": ([(1, 0, 7), (2, 0, 5), (-2, 1, 3), (-1, 1, 5), (0, 1, 7), (1, 1, 5), (2, 1, 3),
                             (-2, 2, 1), (-1, 2, 3), (0, 2, 5), (1, 2, 3), (2, 2, 1)], 48),
    "DIFFUSION_ROW": ([(1, 0, 1)], 1),
    "DIFFUSION_COLUMN": ([(0, 1, 1)], 1),
    "DIFFUSION_2D": ([(1, 0, 1), (0, 1, 1)], 2),
}

_BAYER8 = [
    [0, 32, 8, 40, 2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21],
]

# ---------- 字体 ----------

_FONT_CANDIDATES = [
    getattr(config, "FONT_PATH", "") or "",
    "fonts/LXGWHeartSerifMN.ttf",
    "fonts/LXGWWenKai-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]


@lru_cache(maxsize=32)
def _load_font(size: int):
    for candidate in _FONT_CANDIDATES:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_absolute():
            path = Path(__file__).parent / candidate
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default(size)


# ---------- 基础工具 ----------

def _cover_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left = (img.width - w) // 2
    top = (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def _ink_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    """实际墨迹宽度（textlength 只算步进宽度，小字号 CJK 会偏窄导致溢出）。"""
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    if not text:
        return ""
    if _ink_width(draw, text, font) <= max_w:
        return text
    while text and _ink_width(draw, text + "…", font) > max_w:
        text = text[:-1]
    return text + "…"


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    for raw in text.split("\n"):
        line = ""
        for ch in raw:
            if line and _ink_width(draw, line + ch, font) > max_w:
                lines.append(line)
                line = ch
            else:
                line += ch
        lines.append(line)
    return lines


def _photo_gray(img: Image.Image, w: int, h: int) -> Image.Image:
    photo = _cover_crop(img.convert("RGB"), w, h)
    return ImageOps.autocontrast(photo, cutoff=1).convert("L")


# ---------- 灰度合成（未抖动，文字纯黑） ----------

def _compose_wide(img: Image.Image, caption: str, date_text: str, place: str) -> Image.Image:
    canvas = Image.new("L", (CANVAS_W, CANVAS_H), PAPER)
    canvas.paste(_photo_gray(img, CANVAS_W, PHOTO_H), (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, PHOTO_H, CANVAS_W, CANVAS_H], fill=PAPER)
    pad = 8
    cap_font = _load_font(13)
    meta_font = _load_font(10)
    if caption:
        draw.text((pad, PHOTO_H + 3), _truncate(draw, caption, cap_font, CANVAS_W - pad * 2),
                  font=cap_font, fill=INK)
    meta = "  ·  ".join(x for x in (date_text, place) if x)
    if meta:
        bbox = draw.textbbox((0, 0), meta, font=meta_font)
        x = CANVAS_W - pad - bbox[2]  # 按墨迹右缘对齐
        draw.text((x, CANVAS_H - 13), meta, font=meta_font, fill=INK)
    return canvas


def _compose_tall(img: Image.Image, caption: str, date_text: str, place: str) -> Image.Image:
    canvas = Image.new("L", (CANVAS_W, CANVAS_H), PAPER)
    box_w = min(round(CANVAS_H * img.width / img.height), PHOTO_BOX_MAX_W)
    canvas.paste(_photo_gray(img, box_w, CANVAS_H), (0, 0))
    draw = ImageDraw.Draw(canvas)
    x0 = box_w + 10
    text_w = CANVAS_W - x0 - 8
    cap_font = _load_font(13)
    meta_font = _load_font(10)
    if caption:
        lines = _wrap_text(draw, caption, cap_font, text_w)
        max_lines = 5
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            last = lines[-1]
            while last and _ink_width(draw, last + "…", cap_font) > text_w:
                last = last[:-1]
            lines[-1] = last + "…"
        y = 10
        for line in lines:
            draw.text((x0, y), line, font=cap_font, fill=INK)
            y += 19
    meta = "  ·  ".join(x for x in (date_text, place) if x)
    if meta:
        draw.text((x0, CANVAS_H - 14), _truncate(draw, meta, meta_font, text_w),
                  font=meta_font, fill=INK)
    return canvas


# ---------- 抖动（对整幅灰度画布应用；纯黑/纯白区域误差为 0，文字不受影响） ----------

def _apply_dither(canvas: Image.Image, dither_type: str, dither_kernel: str) -> Image.Image:
    if dither_type == "ORDERED":
        return _dither_ordered(canvas)
    if dither_type == "NONE":
        return canvas.point(lambda p: 255 if p > 128 else 0)
    return _dither_error_diffusion(canvas, dither_kernel)


def _dither_error_diffusion(canvas: Image.Image, kernel_name: str) -> Image.Image:
    if kernel_name == "FLOYD_STEINBERG":
        # PIL 内置 FS 走 C 实现，结果与手写矩阵一致
        return canvas.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    kernel, divisor = _ERROR_KERNELS.get(kernel_name, _ERROR_KERNELS["FLOYD_STEINBERG"])
    out = canvas.convert("L").copy()
    px = out.load()
    w, h = out.size
    for y in range(h):
        for x in range(w):
            old = px[x, y]
            new = 255 if old >= 128 else 0
            err = old - new
            px[x, y] = new
            if err == 0:
                continue
            for dx, dy, wt in kernel:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    v = px[nx, ny] + err * wt // divisor
                    px[nx, ny] = 0 if v < 0 else (255 if v > 255 else v)
    return out.convert("1")


def _dither_ordered(canvas: Image.Image) -> Image.Image:
    out = canvas.copy()
    px = out.load()
    w, h = out.size
    for y in range(h):
        row = _BAYER8[y % 8]
        for x in range(w):
            px[x, y] = 255 if px[x, y] > row[x % 8] * 4 else 0
    return out.convert("1")


def _draw_border(bw: Image.Image, border: int) -> Image.Image:
    """边框由设备绘制在屏幕边缘（官方 API 无"无边框"选项，0=白 1=黑）。

    白色边框在白底画布上不可见，预览不绘制以免遮挡图片；
    黑色边框以 2px 细线叠加提示，推送的灰度图始终不带边框。
    """
    if border == 1:
        draw = ImageDraw.Draw(bw)
        draw.rectangle([0, 0, CANVAS_W - 1, CANVAS_H - 1], outline=INK, width=2)
    return bw


# ---------- 对外接口 ----------

def render_photo(
    image: Image.Image,
    caption: str = "",
    date_text: str = "",
    place: str = "",
    dither_type: str = "DIFFUSION",
    dither_kernel: str = "FLOYD_STEINBERG",
    border: int = 0,
) -> Image.Image:
    """1-bit 预览图。版式按宽高比自适应；本地模拟设备侧抖动与边框。"""
    img = ImageOps.exif_transpose(image)
    if img.width / img.height >= SIDE_TEXT_MAX_ASPECT:
        gray = _compose_wide(img, caption, date_text, place)
    else:
        gray = _compose_tall(img, caption, date_text, place)
    bw = _apply_dither(gray, dither_type, dither_kernel)
    return _draw_border(bw.convert("L"), border).convert("1")


def render_push_png(
    image: Image.Image,
    caption: str = "",
    date_text: str = "",
    place: str = "",
) -> bytes:
    """推送用灰度 PNG：不本地抖动、不画边框，由设备按 ditherType/border 参数处理。"""
    img = ImageOps.exif_transpose(image)
    if img.width / img.height >= SIDE_TEXT_MAX_ASPECT:
        gray = _compose_wide(img, caption, date_text, place)
    else:
        gray = _compose_tall(img, caption, date_text, place)
    out = io.BytesIO()
    gray.save(out, format="PNG", optimize=True)
    return out.getvalue()


def render_png(image: Image.Image, **kwargs) -> bytes:
    out = io.BytesIO()
    render_photo(image, **kwargs).save(out, format="PNG", optimize=True)
    return out.getvalue()
