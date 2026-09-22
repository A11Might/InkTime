"""Dot. Quote/0 推送核心：渲染 → 上传 → 落库 → 生成碰一碰页面。

供两处复用：
- app.py  /api/push（控制台手动推送）
- daily_push.py（定时自动推送）
"""
from __future__ import annotations

import base64
import json
import socket
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

import requests as http_client
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).parent))
try:
    import config
except ImportError:
    import config_example as config  # type: ignore

ROOT = Path(__file__).parent
MOCK_DB = ROOT / "mock" / "photos.db"
DB_PATH = Path(getattr(config, "DB_PATH", "") or MOCK_DB)

# 推送凭证：在 config.py 里配置
DOT_API_KEY = getattr(config, "DOT_API_KEY", "")
DOT_DEVICE_ID = getattr(config, "DOT_DEVICE_ID", "")
DOT_TASK_KEY = getattr(config, "DOT_TASK_KEY", "")  # 设备上有多个「图像 API」内容时指定推给哪个
DOT_TASK_ALIAS = getattr(config, "DOT_TASK_ALIAS", "")  # 可选：改任务在 Dot. App 列表里的显示名
DOT_API_BASE = getattr(config, "DOT_API_BASE", "https://dot.mindreset.tech")

PORT = 8788
OUTPUT_DIR = ROOT / "output"  # 每次推送一个 <id>.*：成图 / 预览 / 原图 / 元数据

PUSH_TABLE_SQL = """CREATE TABLE IF NOT EXISTS push_history (
    pid TEXT PRIMARY KEY,
    photo_path TEXT,
    caption TEXT,
    date_text TEXT,
    place TEXT,
    pushed_at TEXT
)"""


def record_push(pid: str, photo_path: str, caption: str, date_text: str,
                place: str, pushed_at: str) -> None:
    """推送成功后落库：每个 pid 一行，用于「这张照片推过没有/推过几次」。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(PUSH_TABLE_SQL)
        conn.execute(
            "INSERT INTO push_history VALUES (?, ?, ?, ?, ?, ?)",
            (pid, photo_path, caption, date_text, place, pushed_at),
        )
        conn.commit()
    finally:
        conn.close()


def push_info(photo_path: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(PUSH_TABLE_SQL)
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(pushed_at) AS last "
            "FROM push_history WHERE photo_path = ?",
            (photo_path,),
        ).fetchone()
        return {"count": row["n"], "last": row["last"] or ""}
    finally:
        conn.close()


def lan_ip() -> str:
    """局域网 IP（手机碰一碰打开链接用），取不到就回退本机回环。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


PUSH_ERROR_HINTS = {
    400: "参数错误：图片超 3MB 或参数无效",
    403: "权限不足：API Key 无效或不属于该设备",
    404: "设备不存在，或未把「图像 API」加入循环任务",
    500: "设备响应失败：请确认设备已接电源并联网",
}


def push_photo(path: Path, caption: str = "", date_text: str = "",
               place: str = "", border: int = 0,
               refresh_now: bool = True) -> dict:
    """渲染一张照片并推送到 Dot. 设备，成功后写推送历史与碰一碰页面。

    返回 {"ok", "message", "status", "pid", "page", "push_count"}；
    失败时 ok=False，status 为建议的 HTTP 状态码（脚本场景只看 ok/message）。
    """
    import render as renderer  # 同目录模块

    if not DOT_API_KEY or not DOT_DEVICE_ID:
        return {"ok": False, "status": 400,
                "message": "未配置设备：请在 config.py 里填写 DOT_API_KEY / DOT_DEVICE_ID 后重启"}

    try:
        img = Image.open(path)
    except Exception:
        return {"ok": False, "status": 404, "message": f"无法读取图片：{path}"}

    # 推送图已在本地按 Bayer 抖动成 1-bit，设备侧不再二次抖动
    png = renderer.render_push_png(img, caption=caption, date_text=date_text, place=place)

    pid = uuid.uuid4().hex[:10]
    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / f"{pid}.png").write_bytes(png)

    orig = ImageOps.exif_transpose(img.convert("RGB"))
    if max(orig.size) > 2000:
        orig.thumbnail((2000, 2000), Image.LANCZOS)
    orig.save(OUTPUT_DIR / f"{pid}_orig.jpg", quality=86)

    info = {}
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM photo_scores WHERE path = ?", (str(path),)
        ).fetchone()
        if row:
            info = dict(row)
    finally:
        conn.close()
    pushed_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    meta = {
        "id": pid,
        "caption": caption, "place": place, "date": date_text,
        "type": info.get("type") or "未分类",
        "city": info.get("exif_city") or "",
        "memory": info.get("memory_score") or 0,
        "beauty": info.get("beauty_score") or 0,
        "w": orig.width, "h": orig.height,
        "ts": pushed_at,
        "push_count": push_info(str(path))["count"] + 1,  # 含本次
    }
    (OUTPUT_DIR / f"{pid}.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    payload: dict = {
        "refreshNow": refresh_now,
        "image": base64.b64encode(png).decode(),
        "border": border,
        # 图像已本地预抖动成 1-bit，设备侧 ditherType 对其恒等，固定 NONE
        "ditherType": "NONE",
    }
    if DOT_TASK_KEY:
        payload["taskKey"] = DOT_TASK_KEY
    if DOT_TASK_ALIAS:
        payload["taskAlias"] = DOT_TASK_ALIAS
    # 碰一碰链接自动指向这次推送的唯一页面（手机 NFC 触碰后打开）
    ip = lan_ip()
    if ip != "127.0.0.1":
        payload["link"] = f"http://{ip}:{PORT}/memory/{pid}"

    url = f"{DOT_API_BASE.rstrip('/')}/api/authV2/open/device/{DOT_DEVICE_ID}/image"
    try:
        resp = http_client.post(
            url,
            headers={"Authorization": f"Bearer {DOT_API_KEY}"},
            json=payload,
            timeout=30,
        )
    except http_client.RequestException:
        return {"ok": False, "status": 502, "message": "无法连接 Dot. 服务，请检查网络"}

    if resp.status_code == 200:
        # push_history 里加上的这一行，恰好就是 meta 里预写的 push_count
        record_push(pid, str(path), caption, date_text, place, pushed_at)
        msg = "已保存，设备唤醒后显示" if not refresh_now else "已推送到设备，屏幕刷新中"
        return {"ok": True, "status": 200, "message": msg, "pid": pid,
                "page": f"/memory/{pid}", "push_count": meta["push_count"]}

    hint = PUSH_ERROR_HINTS.get(resp.status_code, f"推送失败（HTTP {resp.status_code}）")
    try:
        detail = resp.json().get("message", "")
    except ValueError:
        detail = ""
    message = f"{hint}：{detail}" if detail else hint
    return {"ok": False, "status": resp.status_code, "message": message}
