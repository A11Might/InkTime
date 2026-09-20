"""InkTime 可视化控制台后端。

优先读 config.py 指向的真实 photos.db / IMAGE_DIR；
没有则回落到 mock/photos.db，开箱即用。
"""
from __future__ import annotations

import base64
import json
import os
import socket
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

import requests as http_client
from flask import Flask, abort, jsonify, render_template, request, send_file
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).parent))
try:
    import config
except ImportError:
    import config_example as config  # type: ignore

ROOT = Path(__file__).parent
MOCK_DB = ROOT / "mock" / "photos.db"
MOCK_DIR = ROOT / "mock" / "photos"

DB_PATH = Path(getattr(config, "DB_PATH", "") or MOCK_DB)
IMAGE_DIR = Path(getattr(config, "IMAGE_DIR", "") or MOCK_DIR)

# 推送凭证：环境变量优先，config.py 留空兜底
DOT_API_KEY = os.environ.get("DOT_API_KEY") or getattr(config, "DOT_API_KEY", "")
DOT_DEVICE_ID = os.environ.get("DOT_DEVICE_ID") or getattr(config, "DOT_DEVICE_ID", "")
DOT_TASK_KEY = os.environ.get("DOT_TASK_KEY") or getattr(config, "DOT_TASK_KEY", "")
DOT_API_BASE = os.environ.get("DOT_API_BASE") or getattr(config, "DOT_API_BASE", "https://dot.mindreset.tech")

PORT = 8788
OUTPUT_DIR = ROOT / "output"  # 每次推送一个 <id>.*：成图 / 预览 / 原图 / 元数据

DITHER_TYPES = {"DIFFUSION", "ORDERED", "NONE"}
DITHER_KERNELS = {"THRESHOLD", "ATKINSON", "BURKES", "FLOYD_STEINBERG", "SIERRA2",
                  "STUCKI", "JARVIS_JUDICE_NINKE", "DIFFUSION_ROW", "DIFFUSION_COLUMN",
                  "DIFFUSION_2D"}

app = Flask(__name__)
import render as renderer  # noqa: E402  (同目录模块)


def db_rows(sql: str, args: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


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


def photo_date(row: dict) -> str:
    dt = row.get("exif_datetime")
    if dt:
        return str(dt)
    try:
        exif = json.loads(row.get("exif_json") or "{}")
        return str(exif.get("datetime") or "")
    except (ValueError, TypeError):
        return ""


def serialize(row: dict) -> dict:
    name = Path(row["path"]).name
    return {
        "path": row["path"],
        "name": name,
        "caption": row.get("caption") or "",
        "type": row.get("type") or "未分类",
        "memory": row.get("memory_score") or 0,
        "beauty": row.get("beauty_score") or 0,
        "date": photo_date(row)[:16],
        "city": row.get("exif_city") or "",
        "w": row.get("width") or 0,
        "h": row.get("height") or 0,
    }


def resolve_image(rel_or_abs: str) -> Path:
    p = Path(rel_or_abs)
    if not p.is_absolute():
        p = IMAGE_DIR / rel_or_abs
        if not p.exists():
            p = ROOT / rel_or_abs  # 兼容相对仓库根的写法（如 mock/photos/x.jpg）
    p = p.resolve()
    if IMAGE_DIR.resolve() not in p.parents:
        abort(403)
    if not p.exists():
        abort(404)
    return p


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/stats")
def stats():
    today = datetime.now().strftime("%m-%d")
    agg = db_rows(
        """SELECT COUNT(*) AS total,
                  COALESCE(AVG(memory_score), 0) AS avg_memory,
                  COALESCE(SUM(memory_score >= 85), 0) AS high_count
           FROM photo_scores"""
    )[0]
    picks = db_rows(
        """SELECT *, strftime('%m-%d', exif_datetime) AS md
           FROM photo_scores
           WHERE md = ?
           ORDER BY memory_score DESC LIMIT 3""",
        (today,),
    )
    return jsonify({
        "total": agg["total"],
        "avg_memory": round(agg["avg_memory"]),
        "high_count": agg["high_count"],
        "today": today,
        "db_name": DB_PATH.name,
        "picks": [serialize(r) for r in picks],
    })


@app.get("/api/photos")
def photos():
    typ = request.args.get("type", "")
    sort = request.args.get("sort", "memory")
    q = request.args.get("q", "").strip()
    order = {"memory": "memory_score DESC", "beauty": "beauty_score DESC",
             "date": "exif_datetime DESC"}.get(sort, "memory_score DESC")
    sql, args = "SELECT * FROM photo_scores", []
    conds = []
    if typ and typ != "全部":
        conds.append("type = ?")
        args.append(typ)
    if q:
        conds.append("(caption LIKE ? OR exif_city LIKE ? OR type LIKE ?)")
        args += [f"%{q}%"] * 3
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += f" ORDER BY {order} LIMIT 500"
    return jsonify([serialize(r) for r in db_rows(sql, tuple(args))])


@app.get("/api/types")
def types():
    rows = db_rows("SELECT DISTINCT type FROM photo_scores ORDER BY type")
    return jsonify(["全部"] + [r["type"] for r in rows if r["type"]])


@app.get("/api/thumb")
def thumb():
    p = resolve_image(request.args.get("path", ""))
    img = Image.open(p)
    img.thumbnail((560, 560))
    import io
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=84)
    buf.seek(0)
    return send_file(buf, mimetype="image/jpeg", max_age=3600)


@app.get("/api/render")
def render_api():
    p = resolve_image(request.args.get("path", ""))
    caption = request.args.get("caption", "")
    dither_type = request.args.get("ditherType", "DIFFUSION").upper()
    dither_kernel = request.args.get("ditherKernel", "FLOYD_STEINBERG").upper()
    if dither_type not in DITHER_TYPES:
        dither_type = "DIFFUSION"
    if dither_kernel not in DITHER_KERNELS:
        dither_kernel = "FLOYD_STEINBERG"
    border = 1 if request.args.get("border") == "1" else 0
    img = Image.open(p)
    png = renderer.render_png(
        img, caption=caption,
        date_text=request.args.get("date", "")[:10],
        place=request.args.get("place", ""),
        dither_type=dither_type, dither_kernel=dither_kernel, border=border,
    )
    import io
    return send_file(io.BytesIO(png), mimetype="image/png", max_age=60)


@app.get("/api/pushinfo")
def pushinfo_api():
    """某张照片的历史推送记录：次数 + 最近一次时间。"""
    p = resolve_image(request.args.get("path", ""))
    return jsonify(push_info(str(p)))


# ---------- 推送到 Quote/0 ----------

PUSH_ERROR_HINTS = {
    400: "参数错误：图片超 3MB 或参数无效",
    403: "权限不足：API Key 无效或不属于该设备",
    404: "设备不存在，或未把「图像 API」加入循环任务",
    500: "设备响应失败：请确认设备已接电源并联网",
}


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


@app.get("/s/<pid>")
def tap_page(pid: str):
    """碰一碰（NFC）打开的页面：一次推送的原图与信息。"""
    meta_file = OUTPUT_DIR / f"{pid}.json"
    if not meta_file.exists():
        abort(404)
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    return render_template("tap.html", **meta)


@app.get("/img/<pid>.png")
def push_img(pid: str):
    f = OUTPUT_DIR / f"{pid}.png"
    if not f.exists():
        abort(404)
    return send_file(f, mimetype="image/png", max_age=3600)


@app.get("/img/<pid>_orig.jpg")
def push_orig(pid: str):
    f = OUTPUT_DIR / f"{pid}_orig.jpg"
    if not f.exists():
        abort(404)
    return send_file(f, mimetype="image/jpeg", max_age=3600)


@app.post("/api/push")
def push_api():
    data = request.get_json(force=True, silent=True) or {}
    p = resolve_image(data.get("path", ""))

    if not DOT_API_KEY or not DOT_DEVICE_ID:
        return jsonify({
            "ok": False,
            "message": "未配置设备：请设置环境变量 DOT_API_KEY / DOT_DEVICE_ID 后重启",
        }), 400

    border = 1 if str(data.get("border")) == "1" else 0

    img = Image.open(p)
    caption = str(data.get("caption", ""))
    date_text = str(data.get("date", ""))[:10]
    place = str(data.get("place", ""))

    # 推送图已在本地按 Bayer 抖动成 1-bit，设备侧不再二次抖动
    png = renderer.render_push_png(img, caption=caption, date_text=date_text, place=place)

    pid = uuid.uuid4().hex[:10]
    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / f"{pid}.png").write_bytes(png)

    orig = ImageOps.exif_transpose(img.convert("RGB"))
    if max(orig.size) > 2000:
        orig.thumbnail((2000, 2000), Image.LANCZOS)
    orig.save(OUTPUT_DIR / f"{pid}_orig.jpg", quality=86)

    info = db_rows("SELECT * FROM photo_scores WHERE path = ?", (str(p),))
    info = info[0] if info else {}
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
        "push_count": push_info(str(p))["count"] + 1,  # 含本次
    }
    (OUTPUT_DIR / f"{pid}.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    payload: dict = {
        "refreshNow": bool(data.get("refreshNow", True)),
        "image": base64.b64encode(png).decode(),
        "border": border,
        # 图像已本地预抖动成 1-bit，设备侧 ditherType 对其恒等，固定 NONE
        "ditherType": "NONE",
    }
    if DOT_TASK_KEY:
        payload["taskKey"] = DOT_TASK_KEY
    # 碰一碰链接自动指向这次推送的唯一页面（手机 NFC 触碰后打开）
    ip = lan_ip()
    if ip != "127.0.0.1":
        payload["link"] = f"http://{ip}:{PORT}/s/{pid}"

    url = f"{DOT_API_BASE.rstrip('/')}/api/authV2/open/device/{DOT_DEVICE_ID}/image"
    try:
        resp = http_client.post(
            url,
            headers={"Authorization": f"Bearer {DOT_API_KEY}"},
            json=payload,
            timeout=30,
        )
    except http_client.RequestException:
        return jsonify({"ok": False, "message": "无法连接 Dot. 服务，请检查网络"}), 502

    if resp.status_code == 200:
        record_push(pid, str(p), caption, date_text, place, pushed_at)
        meta["push_count"] = push_info(str(p))["count"]
        (OUTPUT_DIR / f"{pid}.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        msg = "已保存，设备唤醒后显示" if not payload["refreshNow"] else "已推送到设备，屏幕刷新中"
        return jsonify({"ok": True, "message": msg, "page": f"/s/{pid}",
                        "push_count": meta["push_count"]})

    hint = PUSH_ERROR_HINTS.get(resp.status_code, f"推送失败（HTTP {resp.status_code}）")
    try:
        detail = resp.json().get("message", "")
    except ValueError:
        detail = ""
    message = f"{hint}：{detail}" if detail else hint
    return jsonify({"ok": False, "message": message}), resp.status_code


if __name__ == "__main__":
    if not DB_PATH.exists():
        sys.exit(f"数据库不存在: {DB_PATH}（先跑 python mock/seed_mock.py，或在 config.py 配置 DB_PATH）")
    # 绑定 0.0.0.0：手机碰一碰打开 /s/<id> 页面需要局域网可达
    app.run(host="0.0.0.0", port=PORT, debug=True)
