"""每日选片并推送到 Dot. Quote/0。

选片逻辑与上游 dai-hongtao/InkTime 一致：单张照片、纯随机、无推送记忆。
- 从今天往前找第一个有达标照片（回忆分 ≥ MEMORY_THRESHOLD）的月-日（跨年，
  最多回退一年），在当天的达标照片里随机挑 DAILY_COUNT 张；
- 一年都没有达标照片，就用全库回忆分最高的前 10 名兜底；
- 不记录、不排除推过的照片——随机到重复的就是重复的。
相似照片分叠只用于画廊聚合浏览（卡牌堆/封面），不参与推送。

定时推送长在 app.py 服务里（AUTO_PUSH）；这个文件是选片逻辑本体 +
手动命令行（测试选片、补推一张时用）：

    python3.11 daily_push.py --dry-run           # 只显示会推哪张，不真推
    python3.11 daily_push.py --date 2001-02-03   # 模拟别的日子
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    import config
except ImportError:
    import config_example as config  # type: ignore

import dot_push

MEMORY_THRESHOLD = float(getattr(config, "MEMORY_THRESHOLD", 70))  # 回忆分达标线
DAILY_COUNT = int(getattr(config, "DAILY_COUNT", 1))  # 每天推几张


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def parse_date(row: dict) -> date | None:
    """拍摄日期：exif_datetime 优先，空了退回 exif_json 里的 datetime。

    兼容 2024-09-21 与 EXIF 冒号风格 2024:09:21 两种前缀。
    """
    raw = str(row["exif_datetime"] or "").strip()
    if not raw:
        try:
            raw = str((json.loads(row["exif_json"] or "{}")).get("datetime") or "")
        except (ValueError, TypeError):
            raw = ""
    raw = raw[:10].replace(":", "-")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def load_pool(db_path: Path) -> tuple[list[dict], int]:
    """全库候选：带可解析拍摄日期、非截图的照片。总数用于日志展示。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("ALTER TABLE photo_scores ADD COLUMN stack_id INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE photo_scores ADD COLUMN stack_cover INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        rows = conn.execute(
            "SELECT path, exif_datetime, exif_json, memory_score, side_caption, caption, "
            "exif_city, stack_id, stack_cover FROM photo_scores").fetchall()
    finally:
        conn.close()
    total = len(rows)
    pool = []
    for r in rows:
        if "screenshot" in str(r["path"]).lower():
            continue
        d = parse_date(r)
        if d is None:
            continue
        pool.append({
            "path": r["path"],
            "d": d,
            "md": d.strftime("%m-%d"),
            "memory": r["memory_score"] or 0,
            "caption": r["side_caption"] or r["caption"] or "",
            "city": r["exif_city"] or "",
            "stack": r["stack_id"],           # 相似分叠 id（None = 单张）
            "cover": bool(r["stack_cover"]),  # 是否被选为这叠的封面
        })
    return pool, total


def day_candidates(day, pool) -> list[dict]:
    """某月-日的达标照片（回忆分 ≥ 阈值，跨年同月-日）。
    推送以单张为单位；相似分叠只在画廊里聚合浏览，不参与推送。"""
    md = day.strftime("%m-%d")
    return [p for p in pool if p["md"] == md and p["memory"] >= MEMORY_THRESHOLD]


def resolve_day(target: date, pool: list[dict]) -> date | None:
    """「历史上的今天」落在哪天：从今天往前一天天回退（最多一年），
    第一个有达标照片的月-日就是它。选片不带任何推送记忆——今天有照片，
    哪怕昨天刚推过同一张，今天照样从今天挑（上游式纯随机）。"""
    for offset in range(0, 366):
        d = target - timedelta(days=offset)
        if day_candidates(d, pool):
            return d
    return None


def select(db_path: Path, target: date, count: int = DAILY_COUNT) -> list[tuple[dict, str]]:
    """选出 count 张照片：都来自同一个「历史上的今天」，随机挑。
    CLI 和 app.py 服务内定时共用。"""
    pool, _ = load_pool(db_path)
    if not pool:
        return []
    day = resolve_day(target, pool)
    if day is None:
        top = sorted(pool, key=lambda p: p["memory"], reverse=True)[:10]
        return [(random.choice(top),
                 f"近一年没有达标照片，全库高分兜底（前 {len(top)} 名里随机）")]
    cands = day_candidates(day, pool)
    offset = (target - day).days
    when = "历史上的今天" if offset == 0 else f"历史上的今天（回退 {offset} 天）"
    return [(p, f"{when}，随机挑的") for p in random.sample(cands, min(count, len(cands)))]


def main() -> int:
    ap = argparse.ArgumentParser(description="手动跑一次每日选片推送")
    ap.add_argument("--dry-run", action="store_true", help="只显示选片结果，不真推送")
    ap.add_argument("--date", help="模拟指定日期（YYYY-MM-DD），默认今天")
    ap.add_argument("--db", help="指定数据库路径（默认读 config.py 的 DB_PATH）")
    args = ap.parse_args()

    target = date.fromisoformat(args.date) if args.date else date.today()
    db_path = Path(args.db).expanduser() if args.db else dot_push.DB_PATH
    if args.db:
        dot_push.DB_PATH = db_path.resolve()  # 推送成图跟着库走

    if not db_path.exists():
        log(f"错误：数据库不存在 {db_path}（先跑 analyze_photos.py，或 --db 指定）")
        return 1
    try:
        pool, total = load_pool(db_path)
    except sqlite3.Error:
        log(f"错误：{db_path} 里没有 photo_scores 表（先跑 analyze_photos.py 分析建库）")
        return 1
    log(f"数据库：{db_path}（分析过 {total} 张，其中 {len(pool)} 张带拍摄日期；"
        f"阈值 {MEMORY_THRESHOLD:.0f}，每天 {DAILY_COUNT} 张）")
    if not pool:
        log("错误：没有可用的照片，先跑 analyze_photos.py 分析一批。")
        return 1

    if args.dry_run:
        log(f"[dry-run] 以下是 {target.isoformat()} 会推送的照片，不真推：")
    else:
        log(f"开始为 {target.isoformat()} 选片推送……")

    picks = select(db_path, target)
    if not picks:
        log("错误：挑不出照片。")
        return 1
    ok = True
    for i, (photo, why) in enumerate(picks, 1):
        name = Path(photo["path"]).name
        extra = "，".join(x for x in (photo["d"].isoformat(), photo["city"]) if x)
        log(f"第 {i}/{len(picks)} 张：{name}（回忆分 {photo['memory']:.0f}，{extra}）—— {why}")
        log(f"旁白：{photo['caption'] or '（无）'}")
        if args.dry_run:
            continue
        r = dot_push.push_photo(
            Path(photo["path"]),
            caption=photo["caption"],
            date_text=photo["d"].isoformat(),
            place=photo["city"],
        )
        log(f"→ {r['message']}" + (f"，碰一碰页：http://<本机IP>:{dot_push.PORT}{r['page']}" if r["ok"] else ""))
        if not r["ok"]:
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
