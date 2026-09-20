"""296x152 黑白墨水屏渲染器（Quote/0 规格）版式（实机比选确定）。

版式（2026-09 在 Quote/0 实机上逐版比选确定）：照片一律顶格铺满左侧、
文字全部排在右侧白区，不裁主体、文字绝不压图：
- 横图（宽高比 >= SIDE_TEXT_MAX_ASPECT）：照片等比放进 203x152 顶满左侧
  （4:3 恰好铺满，其余比例白边补齐，永不裁剪），右文字条 80px：
  旁白 16px 每行 5 字（至多 4 行），地点 12px、日期 12px 依次排在右下。
- 竖图/方图：照片 cover 裁剪 116x152 顶满左侧（3:4 几乎零裁剪），
  右文字列 162px：旁白 16px 每行 10 字（至多 5 行），日期靠左、地点右对齐同一行。

抖动锁定 Bayer 8x8 有序抖动（实机比选结论：小尺寸 1-bit 上比误差扩散干净稳定）。
文字在灰度画布上即为纯黑（0/255），有序抖动对纯黑纯白是恒等变换，文字永远锐利。
推送输出 = 本地 Bayer 抖动后的 1-bit 图，设备侧任何 ditherType 都是恒等变换，
所见即所得。

render_photo()   → 1-bit 预览图（可选边框模拟；dither 参数仅供预览实验）
render_push_png()→ Bayer 1-bit PNG（推送用，与实机验证效果一致）
"""
from __future__ import annotations

import io
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

sys.path.insert(0, str(Path(__file__).parent))
try:
    import config
except ImportError:
    import config_example as config  # type: ignore

CANVAS_W = getattr(config, "CANVAS_W", 296)
CANVAS_H = getattr(config, "CANVAS_H", 152)

# 宽高比低于此值的照片（竖图/方图）走「照片靠左 + 右侧文字栏」版式。
# iPhone 照片全是 4:3（横 1.333 / 竖 0.75）：阈值取 1.3，
# 4:3 横图仍走横图版式，竖图和方图走侧排。
SIDE_TEXT_MAX_ASPECT = 1.3

# 横图版式：照片等比放进 203x152 顶满左侧，右文字条 x=208 宽 80（每行 5 字）
WIDE_PHOTO_W = getattr(config, "WIDE_PHOTO_W", 203)
WIDE_TEXT_X = getattr(config, "WIDE_TEXT_X", 208)
WIDE_TEXT_W = CANVAS_W - WIDE_TEXT_X - 8
WIDE_CAPTION_Y, WIDE_PLACE_Y, WIDE_DATE_Y = 22, 114, 132
WIDE_MAX_LINES = 4

# 竖图版式：照片 cover 116x152 顶满左侧，右文字列 x=126 宽 162（每行 10 字）
TALL_PHOTO_W = getattr(config, "TALL_PHOTO_W", 116)
TALL_TEXT_X = getattr(config, "TALL_TEXT_X", 126)
TALL_TEXT_W = CANVAS_W - TALL_TEXT_X - 8
TALL_CAPTION_Y, TALL_META_Y, TALL_LINE_H = 18, 130, 22
TALL_MAX_LINES = 5

CAPTION_SIZE, META_SIZE, CAPTION_LINE_H = 16, 12, 22

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
    "/Users/kohath/Library/Fonts/LXGWWenKaiGBScreen.ttf",  # 实机验证所用字体
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

def _enhance(photo: Image.Image) -> Image.Image:
    """抖动前的对比度增强：黑白屏损失中间调，先拉对比再轻锐化。"""
    photo = ImageOps.autocontrast(photo, cutoff=1)
    return photo.filter(ImageFilter.UnsharpMask(radius=1.2, percent=80, threshold=2))


def _cover_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    """铺满裁剪（竖图版式）：放大到完全覆盖后居中裁剪。"""
    img = ImageOps.exif_transpose(img)
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left = (img.width - w) // 2
    top = (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def _fit_contain(img: Image.Image, w: int, h: int) -> Image.Image:
    """等比放进 w×h 盒子，白底补边居中——永不裁剪（横图版式）。"""
    img = ImageOps.exif_transpose(img)
    scale = min(w / img.width, h / img.height)
    nw, nh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    photo = img.resize((nw, nh), Image.LANCZOS)
    box = Image.new("RGB", (w, h), (255, 255, 255))
    box.paste(photo, ((w - nw) // 2, (h - nh) // 2))
    return box


def _photo_gray_cover(img: Image.Image, w: int, h: int) -> Image.Image:
    return _enhance(_cover_crop(img.convert("RGB"), w, h)).convert("L")


def _photo_gray_fit(img: Image.Image, w: int, h: int) -> Image.Image:
    return _enhance(_fit_contain(img.convert("RGB"), w, h)).convert("L")


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


# 闭合标点不出现在行首（禁则）；必要时允许该行微溢出，吃掉右侧留白
_CLOSING_PUNCT = "。，！？、；：）》」』…"


def _wrap_segment(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for ch in text:
        if line and _ink_width(draw, line + ch, font) > max_w:
            if ch in _CLOSING_PUNCT:
                lines.append(line + ch)
                line = ""
            else:
                lines.append(line)
                line = ch
        else:
            line += ch
    if line:
        lines.append(line)
    for i in range(1, len(lines)):
        if lines[i] and lines[i][0] in _CLOSING_PUNCT:
            lines[i - 1] += lines[i][0]
            lines[i] = lines[i][1:]
    return [l for l in lines if l]


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    for raw in text.split("\n"):
        lines.extend(_wrap_segment(draw, raw, font, max_w))
    return lines


def _limit_lines(draw: ImageDraw.ImageDraw, lines: list[str], font, max_w: int,
                 max_lines: int) -> list[str]:
    if len(lines) <= max_lines:
        return lines
    lines = lines[:max_lines]
    last = lines[-1]
    while last and _ink_width(draw, last + "…", font) > max_w:
        last = last[:-1]
    lines[-1] = last + "…"
    return lines


def _draw_caption(draw: ImageDraw.ImageDraw, caption: str, font, x: int, y: int,
                  max_w: int, max_lines: int) -> None:
    if not caption:
        return
    lines = _limit_lines(draw, _wrap_text(draw, caption, font, max_w), font, max_w, max_lines)
    for ln in lines:
        draw.text((x, y), ln, font=font, fill=INK)
        y += CAPTION_LINE_H


# ---------- 灰度合成（未抖动，文字纯黑） ----------

def _compose_wide(img: Image.Image, caption: str, date_text: str, place: str) -> Image.Image:
    """横图版式：照片 203x152 等比顶满左侧，右文字条 = 旁白 / 地点 / 日期。"""
    canvas = Image.new("L", (CANVAS_W, CANVAS_H), PAPER)
    canvas.paste(_photo_gray_fit(img, WIDE_PHOTO_W, CANVAS_H), (0, 0))
    draw = ImageDraw.Draw(canvas)
    cap_font, meta_font = _load_font(CAPTION_SIZE), _load_font(META_SIZE)
    _draw_caption(draw, caption, cap_font, WIDE_TEXT_X, WIDE_CAPTION_Y,
                  WIDE_TEXT_W, WIDE_MAX_LINES)
    if place:
        draw.text((WIDE_TEXT_X, WIDE_PLACE_Y), _truncate(draw, place, meta_font, WIDE_TEXT_W),
                  font=meta_font, fill=INK)
    if date_text:
        draw.text((WIDE_TEXT_X, WIDE_DATE_Y), date_text, font=meta_font, fill=INK)
    return canvas


def _compose_tall(img: Image.Image, caption: str, date_text: str, place: str) -> Image.Image:
    """竖图版式：照片 cover 116x152 顶满左侧，右文字列 = 旁白 / 日期左 + 地点右。"""
    canvas = Image.new("L", (CANVAS_W, CANVAS_H), PAPER)
    canvas.paste(_photo_gray_cover(img, TALL_PHOTO_W, CANVAS_H), (0, 0))
    draw = ImageDraw.Draw(canvas)
    cap_font, meta_font = _load_font(CAPTION_SIZE), _load_font(META_SIZE)
    _draw_caption(draw, caption, cap_font, TALL_TEXT_X, TALL_CAPTION_Y,
                  TALL_TEXT_W, TALL_MAX_LINES)
    if date_text or place:
        right_edge = TALL_TEXT_X + TALL_TEXT_W
        if place:
            avail = TALL_TEXT_W
            if date_text:
                avail -= _ink_width(draw, date_text, meta_font) + 12
            place = _truncate(draw, place, meta_font, avail)
            bbox = draw.textbbox((0, 0), place, font=meta_font)
            draw.text((right_edge - bbox[2], TALL_META_Y), place, font=meta_font, fill=INK)
        if date_text:
            draw.text((TALL_TEXT_X, TALL_META_Y), date_text, font=meta_font, fill=INK)
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


def _compose_auto(img: Image.Image, caption: str, date_text: str, place: str) -> Image.Image:
    if img.width / img.height >= SIDE_TEXT_MAX_ASPECT:
        return _compose_wide(img, caption, date_text, place)
    return _compose_tall(img, caption, date_text, place)


# ---------- 对外接口 ----------

def render_photo(
    image: Image.Image,
    caption: str = "",
    date_text: str = "",
    place: str = "",
    dither_type: str = "ORDERED",
    dither_kernel: str = "FLOYD_STEINBERG",
    border: int = 0,
) -> Image.Image:
    """1-bit 预览图。版式按宽高比自适应；dither 参数仅供预览实验。"""
    img = ImageOps.exif_transpose(image)
    gray = _compose_auto(img, caption, date_text, place)
    bw = _apply_dither(gray, dither_type, dither_kernel)
    return _draw_border(bw.convert("L"), border).convert("1")


def render_push_png(
    image: Image.Image,
    caption: str = "",
    date_text: str = "",
    place: str = "",
) -> bytes:
    """推送用图：Bayer 1-bit（实机验证过的效果）。设备侧任何 ditherType 对 1-bit 图都是恒等变换。"""
    img = ImageOps.exif_transpose(image)
    bw = _dither_ordered(_compose_auto(img, caption, date_text, place))
    out = io.BytesIO()
    bw.convert("L").save(out, format="PNG", optimize=True)
    return out.getvalue()


def render_png(image: Image.Image, **kwargs) -> bytes:
    out = io.BytesIO()
    render_photo(image, **kwargs).save(out, format="PNG", optimize=True)
    return out.getvalue()
