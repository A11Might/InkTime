"""InkTime 可视化控制台后端。

优先读 config.py 指向的真实 photos.db / IMAGE_DIR；
没有则回落到 mock/photos.db，开箱即用。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
try:
    import config
except ImportError:
    import config_example as config  # type: ignore

ROOT = Path(__file__).parent
MOCK_DIR = ROOT / "mock" / "photos"

# 推送核心（渲染 + Dot API + 落库）在 dot_push.py，与 daily_push.py 共用
from dot_push import (  # noqa: E402
    DB_PATH,
    OUTPUT_DIR,
    PORT,
    PUSH_TABLE_SQL,
    push_info,
    push_photo,
)

IMAGE_DIR = Path(getattr(config, "IMAGE_DIR", "") or MOCK_DIR).expanduser().resolve()

DITHER_TYPES = {"DIFFUSION", "ORDERED", "NONE"}
DITHER_KERNELS = {"THRESHOLD", "ATKINSON", "BURKES", "FLOYD_STEINBERG", "SIERRA2",
                  "STUCKI", "JARVIS_JUDICE_NINKE", "DIFFUSION_ROW", "DIFFUSION_COLUMN",
                  "DIFFUSION_2D"}

app = Flask(__name__)
import render as renderer  # noqa: E402  (同目录模块)
import daily_push as daily  # noqa: E402  (选片逻辑 + 推送历史查询)


def db_rows(sql: str, args: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
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


def normalize_datetime(dt: str) -> str:
    """EXIF 冒号日期 2023:09:02 → 2023-09-02，与库内其余记录风格统一。"""
    dt = str(dt or "").strip()
    if len(dt) >= 10 and dt[4:5] == ":" and dt[7:8] == ":":
        dt = dt[:4] + "-" + dt[5:7] + "-" + dt[8:]
    return dt


def serialize(row: dict) -> dict:
    name = Path(row["path"]).name
    return {
        "path": row["path"],
        "name": name,
        # 屏上文案：取旁白 side_caption（原版分工：caption 为长描述）；旧记录旁白为空时回落 caption
        "caption": row.get("side_caption") or row.get("caption") or "",
        "type": row.get("type") or "未分类",
        "memory": row.get("memory_score") or 0,
        "beauty": row.get("beauty_score") or 0,
        "date": normalize_datetime(photo_date(row))[:16],
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
    # 单机自用工具：允许访问磁盘上任意已存在的图片路径
    # （分析目录可以是照片库之外的任意文件夹，如 ~/Desktop/未命名文件夹）
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
        "auto_push": auto_push_status(),
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
        conds.append("type LIKE ?")
        args.append(f"%{typ}%")
    if q:
        conds.append("(side_caption LIKE ? OR caption LIKE ? OR exif_city LIKE ? OR type LIKE ?)")
        args += [f"%{q}%"] * 4
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += f" ORDER BY {order} LIMIT 500"
    return jsonify([serialize(r) for r in db_rows(sql, tuple(args))])


@app.get("/api/types")
def types():
    """类型标签：一张照片可有多个类型（库内以 / 连接），拆成独立标签去重。"""
    tags, seen = [], set()
    for r in db_rows("SELECT type FROM photo_scores"):
        for t in (r["type"] or "").split("/"):
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                tags.append(t)
    return jsonify(["全部"] + tags)


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


@app.get("/latest")
def s_latest():
    """工作台「碰一碰」标签入口：跳到最近一次推送的展示页。"""
    pid = ""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(PUSH_TABLE_SQL)
        row = conn.execute(
            "SELECT pid FROM push_history ORDER BY pushed_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        pid = row["pid"] if row else ""
    except sqlite3.Error:
        pid = ""
    finally:
        conn.close()
    if not pid:  # 库里没有记录时，回退到 output 目录里最新的推送
        jsons = sorted(OUTPUT_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
        if jsons:
            pid = jsons[-1].stem
    if not pid:
        return redirect("/")
    return redirect(f"/memory/{pid}", code=302)


@app.get("/memory/<pid>")
def memory_page(pid: str):
    """碰一碰（NFC）打开的页面：一次推送的原图与信息。"""
    meta_file = OUTPUT_DIR / f"{pid}.json"
    if not meta_file.exists():
        abort(404)
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    meta["date"] = normalize_datetime(meta.get("date"))
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
    r = push_photo(
        p,
        caption=str(data.get("caption", "")),
        date_text=str(data.get("date", ""))[:10],
        place=str(data.get("place", "")),
        border=1 if str(data.get("border")) == "1" else 0,
        refresh_now=bool(data.get("refreshNow", True)),
    )
    body = {"ok": r["ok"], "message": r["message"]}
    if r["ok"]:
        body["page"] = r["page"]
        body["push_count"] = r["push_count"]
    return jsonify(body), r["status"]


# ---------- 服务内每日定时推送（无需 launchd / crontab）----------

AUTO_PUSH = bool(getattr(config, "AUTO_PUSH", True))
PUSH_HOUR = int(getattr(config, "PUSH_HOUR", 8))      # 每天几点推
PUSH_MINUTE = int(getattr(config, "PUSH_MINUTE", 0))


def auto_push_status() -> dict:
    """给控制台 /api/stats 看的定时推送状态。"""
    return {
        "enabled": AUTO_PUSH,
        "time": f"{PUSH_HOUR:02d}:{PUSH_MINUTE:02d}",
        "pushed_today": _pushed_today(date.today()),
    }


def _pushed_today(day: date) -> bool:
    if not DB_PATH.exists():
        return False
    try:
        return daily.already_pushed_today(DB_PATH, day)
    except sqlite3.Error:
        return False


def _compute_next_run() -> datetime:
    """下一次推送时刻：今天的推送点已过但还没推过就现在补跑；推过或还没到点就排下一个推送点。"""
    now = datetime.now()
    target = now.replace(hour=PUSH_HOUR, minute=PUSH_MINUTE, second=0, microsecond=0)
    if now < target:
        return target
    return now if not _pushed_today(now.date()) else target + timedelta(days=1)


def _auto_push_once(day: date) -> bool:
    picks = daily.select(DB_PATH, day)
    if not picks:
        print("[daily-push] 挑不出照片，跳过", flush=True)
        return True  # 不是暂时性故障，不必重试
    ok = True
    for photo, why in picks:
        name = Path(photo["path"]).name
        extra = "，".join(x for x in (photo["d"].isoformat(), photo["city"]) if x)
        print(f"[daily-push] 选中 {name}（回忆分 {photo['memory']:.0f}，{extra}）—— {why}", flush=True)
        r = push_photo(
            Path(photo["path"]),
            caption=photo["caption"],
            date_text=photo["d"].isoformat(),
            place=photo["city"],
            refresh_now=False,  # 定时推送不强制翻屏：存进设备，等它自己的唤醒周期刷新
        )
        print(f"[daily-push] → {r['message']}", flush=True)
        ok = ok and r["ok"]
    return ok


def _auto_push_loop() -> None:
    next_run = _compute_next_run()
    print(f"[daily-push] 已启用：每天 {PUSH_HOUR:02d}:{PUSH_MINUTE:02d} 自动推送"
          f"（下次 {next_run.strftime('%Y-%m-%d %H:%M')}）", flush=True)
    while True:
        now = datetime.now()
        if now >= next_run:
            if _pushed_today(now.date()):
                next_run = _compute_next_run()  # 今天已经推过（比如手动推了），排下一个推送点
            else:
                try:
                    ok = _auto_push_once(next_run.date())
                except Exception as exc:  # 定时推送出错不能拖垮服务
                    print(f"[daily-push] 出错：{exc}", flush=True)
                    ok = False
                # 失败 10 分钟后重试；成功就排下一个推送点
                next_run = _compute_next_run() if ok else now + timedelta(minutes=10)
        time.sleep(30)


DEBUG_MODE = True  # 控制台改动即时生效；关掉可省一个重载父进程

if __name__ == "__main__":
    if not DB_PATH.exists():
        sys.exit(f"数据库不存在: {DB_PATH}（先跑 python mock/seed_mock.py，或在 config.py 配置 DB_PATH）")
    # debug 模式下 Flask 会先起一个重载父进程再起真正服务的子进程，
    # 只在子进程（WERKZEUG_RUN_MAIN）里启动调度线程，避免跑两份推两次
    if AUTO_PUSH and (os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not DEBUG_MODE):
        threading.Thread(target=_auto_push_loop, daemon=True).start()
    # 绑定 0.0.0.0：手机碰一碰打开 /s/<id> 页面需要局域网可达
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG_MODE)
