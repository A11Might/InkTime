"""每日选片并推送到 Dot. Quote/0。

选片单位是「场景」：相似照片叠（dHash+时间聚类）算一个场景，单张各算一个。
先在「历史上的今天」（月-日相同）里找没回忆过的达标场景；今天没有合格场景就往前
一天天回退，最多回退一年；场景都回忆过了就挑最久没回忆的场景；实在没有就用全库
最高分兜底。推的是场景的封面（用户可在控制台为每叠挑封面）。

定时推送长在 app.py 服务里（AUTO_PUSH）；这个文件是选片逻辑本体 +
手动命令行（测试选片、补推一张时用）：

    python3.11 daily_push.py --dry-run           # 只显示会推哪张，不真推
    python3.11 daily_push.py --date 2001-02-03   # 模拟别的日子
    python3.11 daily_push.py --force             # 今天已推过也再推一张
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


def load_pushed(db_path: Path) -> dict[str, str]:
    """path → 最近一次推送时间；没推过的不在字典里。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(dot_push.PUSH_TABLE_SQL)
        rows = conn.execute(
            "SELECT photo_path, MAX(pushed_at) FROM push_history GROUP BY photo_path"
        ).fetchall()
        return {p: t for p, t in rows if p}
    finally:
        conn.close()


def pick_one(cands: list[dict], pushed: dict[str, str]) -> tuple[dict, str]:
    """从候选里挑一张：优先没推送过的随机挑；都推过则挑最久没推的。"""
    unpushed = [p for p in cands if p["path"] not in pushed]
    if unpushed:
        return random.choice(unpushed), "没推送过的里随机挑"
    return min(cands, key=lambda p: (pushed.get(p["path"], ""), p["path"])), "都推过，挑最久没推的"


def day_scenes(day, pool) -> list[dict]:
    """某月-日的候选按「场景」归组：相似叠算一个场景（封面作代表），单张各算一个。
    场景达标 = 组内任一成员回忆分达阈值。"""
    md = day.strftime("%m-%d")
    cands = [p for p in pool if p["md"] == md and p["memory"] >= MEMORY_THRESHOLD]
    groups: dict = {}
    for c in cands:
        key = c["stack"] if c["stack"] else ("solo", c["path"])
        groups.setdefault(key, []).append(c)
    scenes = []
    for members in groups.values():
        cover = next((m for m in members if m["cover"]), None) \
            or max(members, key=lambda m: m["memory"])
        scenes.append({"cover": cover, "members": members})
    return scenes


def scene_recalled(scene, pushed) -> bool:
    """场景是否回忆过：组内任一成员出现在推送记录里就算（手动推过也算）。"""
    return any(m["path"] in pushed for m in scene["members"])


def scene_last_recall(scene, pushed) -> str:
    return max((pushed.get(m["path"], "") for m in scene["members"]), default="")


def resolve_day(target: date, pool: list[dict], pushed: dict[str, str]) -> date | None:
    """今日选片落在哪天：今天有没回忆过的达标场景就是今天，否则往前一天天回退（最多
    一年）；一年内场景都回忆过了，退回最近有达标场景的那天。找不到 None。"""
    fallback = None
    for offset in range(0, 366):
        d = target - timedelta(days=offset)
        scenes = day_scenes(d, pool)
        if not scenes:
            continue
        if any(not scene_recalled(s, pushed) for s in scenes):
            return d
        if fallback is None:
            fallback = d
    return None


def pick(target: date, pool: list[dict], pushed: dict[str, str]) -> tuple[dict | None, str]:
    """选片主逻辑：场景为候选单位，推的是场景封面。返回 (照片, 选择原因)。"""
    day = resolve_day(target, pool, pushed)
    if day is None:
        fallback = [p for p in pool if p["memory"] >= MEMORY_THRESHOLD] or pool
        if not fallback:
            return None, "全库没有带拍摄日期的照片"
        top = sorted(fallback, key=lambda p: p["memory"], reverse=True)[:10]
        photo, how = pick_one(top, pushed)
        return photo, f"近一年没有达标场景，全库兜底（前 {len(top)} 高分里{how}）"
    offset = (target - day).days
    when = "历史上的今天" if offset == 0 else f"历史上的今天（回退 {offset} 天）"
    scenes = day_scenes(day, pool)
    fresh = [s for s in scenes if not scene_recalled(s, pushed)]
    if fresh:
        scene = random.choice(fresh)
        how = "没回忆过的场景里随机挑"
    else:
        scene = min(scenes, key=lambda s: scene_last_recall(s, pushed))
        how = "场景都回忆过了，挑最久没回忆的"
    return scene["cover"], f"{when}，{how}"


def already_pushed_today(db_path: Path, target: date) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(dot_push.PUSH_TABLE_SQL)
        n = conn.execute(
            "SELECT COUNT(*) FROM push_history WHERE pushed_at LIKE ?",
            (f"{target.isoformat()}%",),
        ).fetchone()[0]
        return n > 0
    finally:
        conn.close()


def select(db_path: Path, target: date, count: int = DAILY_COUNT) -> list[tuple[dict, str]]:
    """选出 count 个场景（推各场景封面）：同场景不重复。CLI 和 app.py 服务内定时共用。"""
    pool, _ = load_pool(db_path)
    pushed = load_pushed(db_path)
    picks: list[tuple[dict, str]] = []
    for _ in range(count):
        photo, why = pick(target, pool, pushed)
        if photo is None:
            break
        picks.append((photo, why))
        # 整个场景（同叠的全部成员）移出候选池，下一张换别的场景
        if photo["stack"]:
            pool = [p for p in pool if p["stack"] != photo["stack"]]
        else:
            pool = [p for p in pool if p["path"] != photo["path"]]
    return picks


def main() -> int:
    ap = argparse.ArgumentParser(description="手动跑一次每日选片推送")
    ap.add_argument("--dry-run", action="store_true", help="只显示选片结果，不真推送")
    ap.add_argument("--date", help="模拟指定日期（YYYY-MM-DD），默认今天")
    ap.add_argument("--force", action="store_true", help="今天已推送过也再推一张")
    ap.add_argument("--db", help="指定数据库路径（默认读 config.py 的 DB_PATH）")
    args = ap.parse_args()

    target = date.fromisoformat(args.date) if args.date else date.today()
    db_path = Path(args.db).expanduser() if args.db else dot_push.DB_PATH
    if args.db:
        dot_push.DB_PATH = db_path.resolve()  # 推送历史跟着库走

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

    if already_pushed_today(db_path, target) and not args.force and not args.date:
        log(f"{target.isoformat()} 已经推送过了，跳过（--force 可强制再推）。")
        return 0

    if args.dry_run:
        log(f"[dry-run] 以下是 {target.isoformat()} 会推送的照片，不真推：")
    else:
        log(f"开始为 {target.isoformat()} 选片推送……")

    ok = True
    picks = select(db_path, target)
    if not picks:
        log("错误：挑不出照片。")
        return 1
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
