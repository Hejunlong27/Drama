#!/usr/bin/env python
"""AI 短剧创作工作台 CLI。

用法（项目根 = 你的剧项目目录，tools/ 固定放在项目根下）：
    python tools/cli.py init --slug parallel-me --title "平行世界的另一个我" --ep 20
    python tools/cli.py import-bible
    python tools/cli.py parse --ep 1
    python tools/cli.py check --ep 1
    python tools/cli.py convert --ep 1
    python tools/cli.py report
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "db" / "drama.db"
BIBLE = ROOT / "bible"
SCRIPT = ROOT / "script"
STORY = ROOT / "storyboard"
LOGS = ROOT / "logs"

DEFAULT_STYLE_LOCK = (
    "cinematic live-action short drama, 35mm anamorphic lens, "
    "desaturated cold-blue palette with warm amber practicals, "
    "high contrast chiaroscuro lighting, subtle film grain, "
    "shallow depth of field, 2.39:1 widescreen, "
    "no watermark, no subtitle, no text overlay, no logo"
)


# ------------------------------------------------------------------ 工具

def _load_project() -> dict:
    p = ROOT / "project.json"
    if not p.exists():
        sys.exit("未找到 project.json，先运行：python tools/cli.py init ...")
    return json.loads(p.read_text(encoding="utf-8"))


def _save_project(cfg: dict) -> None:
    (ROOT / "project.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _slug() -> str:
    return _load_project()["meta"]["slug"]


def _ep_path(ep: int) -> Path:
    return SCRIPT / f"ep_{ep:02d}.md"


def _conn():
    from core import storage
    conn = storage.connect(DB_PATH)
    storage.init_db(conn)
    return conn


def _mkdirs() -> None:
    for d in (BIBLE, SCRIPT, STORY, LOGS, ROOT / "assets" / "char",
              ROOT / "assets" / "scene", ROOT / "gen" / "images",
              ROOT / "gen" / "video", ROOT / "prompts", ROOT / "workflows",
              DB_PATH.parent):
        d.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------ init

def cmd_init(a) -> None:
    from core import storage
    _mkdirs()
    cfg = {
        "meta": {"slug": a.slug, "title": a.title, "platform": a.platform,
                 "total_ep": a.ep, "ep_seconds": a.sec,
                 "style_lock": a.style_lock},
        "artifacts": {"world_bible": "bible/world-bible.json",
                      "outline": "bible/ep_outline.md",
                      "script_dir": "script", "storyboard_dir": "storyboard"},
        "state": {"step": 0, "last_ep": 0, "ep_status": {}},
    }
    _save_project(cfg)
    conn = _conn()
    storage.upsert_project(conn, a.slug, a.title, a.platform, a.ep, a.sec, a.style_lock)
    (BIBLE / "stylelock.md").write_text(
        f"# STYLE LOCK（全剧恒定，禁止逐集改写）\n\n{a.style_lock}\n", encoding="utf-8")
    print(f"[OK] 项目已创建：{ROOT}")
    print(f"   slug={a.slug}  title={a.title}  {a.ep}集 x {a.sec}秒  platform={a.platform}")


# ------------------------------------------------------- import-bible

def cmd_import_bible(a) -> None:
    from core import storage
    from core.convert import world_bible_to_char_anchors, world_bible_to_scenes
    from core.prompt import build_character_sheet

    cfg = _load_project()
    slug = cfg["meta"]["slug"]
    wb = json.loads((ROOT / cfg["artifacts"]["world_bible"]).read_text(encoding="utf-8"))
    conn = _conn()

    chars = world_bible_to_char_anchors(wb)
    for c in chars:
        storage.upsert_character(conn, slug, c)
    scenes = world_bible_to_scenes(wb)
    for s in scenes:
        storage.upsert_scene(conn, slug, s)

    md = ["# 角色视觉锚点表（全剧恒定）", "",
          "| 代号 | 姓名 | 视觉锚点 | 色卡 | 音色 |", "|---|---|---|---|---|"]
    for c in chars:
        c.freeze()
        md.append(f"| `{c.code}` | {c.name} | {c.anchor_text} | {c.palette} | {c.voice} |")
    (BIBLE / "characters.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    md = ["# 场景表", "", "| 代号 | 内/外景 | 时间 | 描述 |", "|---|---|---|---|"]
    for s in scenes:
        md.append(f"| `{s.code}` | {s.int_ext} | {s.time} | {s.desc} |")
    (BIBLE / "scenes.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    for c in chars:
        d = ROOT / "assets" / "char" / c.code
        d.mkdir(parents=True, exist_ok=True)
        (d / "sheet.md").write_text(build_character_sheet(c), encoding="utf-8")

    cfg["state"]["step"] = max(cfg["state"]["step"], 2)
    _save_project(cfg)
    print(f"[OK] 入库完成：{len(chars)} 个角色 / {len(scenes)} 个场景，锚点已冻结")
    for c in chars:
        print(f"   {c.code:<8} {c.name:<6} {c.anchor_text}")


# --------------------------------------------------------------- parse

def cmd_parse(a) -> None:
    from core import storage
    from core.parse import parse_ffs, parse_storyboard

    slug = _slug()
    conn = _conn()
    p = _ep_path(a.ep)
    if not p.exists():
        sys.exit(f"找不到 {p}")
    ep = parse_ffs(p.read_text(encoding="utf-8"), ep_no=a.ep)
    storage.upsert_episode(conn, slug, a.ep, title=ep.title,
                           script_path=str(p.relative_to(ROOT)).replace("\\", "/"),
                           shot_count=len(ep.shots), total_sec=ep.total_sec,
                           status="scripted")
    for s in ep.shots:
        storage.upsert_shot(conn, slug, a.ep, s)
    print(f"[OK] EP{a.ep:02d} 入库：{len(ep.shots)} 镜 / {ep.total_sec} 秒")

    if a.type == "storyboard":
        sp = STORY / f"ep_{a.ep:02d}_storyboard.md"
        if not sp.exists():
            sys.exit(f"找不到 {sp}")
        sb = parse_storyboard(sp.read_text(encoding="utf-8"), ep_no=a.ep)
        storage.upsert_episode(conn, slug, a.ep,
                               storyboard_path=str(sp.relative_to(ROOT)).replace("\\", "/"),
                               status="storyboarded")
        print(f"[OK] 分镜回灌：{len(sb['shots'])} 镜")


# --------------------------------------------------------------- check

def cmd_check(a) -> None:
    from core.parse import parse_ffs, parse_storyboard
    from core.validate import (render_report, validate_script, validate_storyboard)

    slug = _slug()
    conn = _conn()
    p = _ep_path(a.ep)
    if not p.exists():
        sys.exit(f"找不到 {p}")
    raw = p.read_text(encoding="utf-8")
    ep = parse_ffs(raw, ep_no=a.ep)
    issues = validate_script(conn, slug, ep, raw)

    extra = (f"- 镜数：{len(ep.shots)}　- 总时长：{ep.total_sec} 秒")
    sp = STORY / f"ep_{a.ep:02d}_storyboard.md"
    if sp.exists():
        sb = parse_storyboard(sp.read_text(encoding="utf-8"), ep_no=a.ep)
        issues += validate_storyboard(conn, slug, ep, sb)
        extra += f"　- 分镜：{len(sb['shots'])} 镜"

    report = render_report(slug, a.ep, issues, extra)
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / f"validate_ep_{a.ep:02d}.md").write_text(report, encoding="utf-8")
    (LOGS / f"validate_ep_{a.ep:02d}.json").write_text(
        json.dumps([i.as_dict() for i in issues], ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(report)
    n_err = sum(1 for i in issues if i.level == "ERROR")
    sys.exit(1 if n_err else 0)


# ------------------------------------------------------------- convert

def cmd_convert(a) -> None:
    from core.convert import ffs_to_seedance_script
    from core.parse import parse_ffs

    conn = _conn()
    slug = _slug()
    p = _ep_path(a.ep)
    if not p.exists():
        sys.exit(f"找不到 {p}")
    ep = parse_ffs(p.read_text(encoding="utf-8"), ep_no=a.ep)
    scenes = {r["code"]: dict(r) for r in conn.execute(
        "SELECT * FROM scenes WHERE project=?", (slug,))}
    out = ffs_to_seedance_script(ep, scenes)
    STORY.mkdir(parents=True, exist_ok=True)
    dst = STORY / f"ep_{a.ep:02d}_seedance_input.md"
    dst.write_text(out, encoding="utf-8")
    n_dialog = sum(1 for s in ep.shots if s.dialog and s.dialog != "无")
    print(f"[OK] 已生成 {dst}")
    print(f"   场数：{out.count('场') and len([l for l in out.splitlines() if l.startswith('场')])}"
          f"　台词行数应等于：{n_dialog}")


# ------------------------------------------------------------- reindex

def cmd_reindex(a) -> None:
    from core import storage
    from core.parse import parse_ffs

    slug = _slug()
    conn = _conn()
    conn.executescript("DELETE FROM shots; DELETE FROM episodes;")
    n = 0
    for p in sorted(SCRIPT.glob("ep_*.md")):
        ep = int(p.stem.split("_")[1])
        e = parse_ffs(p.read_text(encoding="utf-8"), ep_no=ep)
        storage.upsert_episode(conn, slug, ep, title=e.title,
                               script_path=str(p.relative_to(ROOT)).replace("\\", "/"),
                               shot_count=len(e.shots), total_sec=e.total_sec,
                               status="scripted")
        for s in e.shots:
            storage.upsert_shot(conn, slug, ep, s)
        n += 1
    print(f"[OK] 已从 Markdown 重建索引：{n} 集")


# -------------------------------------------------------------- report

def cmd_report(a) -> None:
    conn = _conn()
    cfg = _load_project()
    slug = cfg["meta"]["slug"]
    p = conn.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
    print(f"# {p['title']}（{slug}）")
    print(f"- 平台：{p['platform']}　集数：{p['total_ep']}　单集：{p['ep_seconds']} 秒")
    print(f"- STYLE LOCK：{p['style_lock'][:80]}...")
    print(f"- 进度：step={cfg['state']['step']}　last_ep={cfg['state']['last_ep']}")
    print("\n## 角色")
    for r in conn.execute("SELECT * FROM characters WHERE project=?", (slug,)):
        print(f"  {r['code']:<8} {r['name']:<8} v{r['anchor_version']}  {r['anchor_text'][:60]}")
    print("\n## 场景")
    for r in conn.execute("SELECT * FROM scenes WHERE project=?", (slug,)):
        print(f"  {r['code']:<8} {r['int_ext']}  {r['desc'][:50]}")
    print("\n## 分集状态")
    for r in conn.execute("SELECT * FROM episodes WHERE project=? ORDER BY ep_no", (slug,)):
        print(f"  EP{r['ep_no']:02d}  {r['status']:<14} {r['shot_count']} 镜 / {r['total_sec']} 秒  {r['title']}")


# --------------------------------------------------- set-anchor / gen

def cmd_set_anchor(a) -> None:
    from core import storage
    from core.schema import Character

    slug = _slug()
    conn = _conn()
    row = storage.get_character(conn, slug, a.code)
    if not row:
        sys.exit(f"未登记代号：{a.code}")
    c = Character(code=row["code"], name=row["name"], anchor_text=a.anchor,
                  palette=row["palette"], voice=row["voice"],
                  lora_path=row["lora_path"], trigger_word=row["trigger_word"],
                  anchor_version=row["anchor_version"] + 1)
    c.freeze()
    conn.execute("UPDATE characters SET anchor_text=?, anchor_hash=?, anchor_version=? "
                 "WHERE project=? AND code=?",
                 (c.anchor_text, c.anchor_hash, c.anchor_version, slug, a.code))
    conn.commit()
    (LOGS / "anchor_changes.log").parent.mkdir(parents=True, exist_ok=True)
    with (LOGS / "anchor_changes.log").open("a", encoding="utf-8") as f:
        f.write(f"v{c.anchor_version} {a.code} reason={a.reason}\n  old: {row['anchor_text']}\n  new: {c.anchor_text}\n")
    print(f"[WARN] {a.code} 锚点更新至 v{c.anchor_version}，请重跑全剧 check")


def cmd_gen(a) -> None:
    print("[stub] gen 尚未实现（Phase 2/3 接 ComfyUI/RunningHub 时启用）")
    print(json.dumps({"engine": a.engine, "ep": a.ep, "shot": a.shot}, ensure_ascii=False))


# ------------------------------------------------------------------ main

def main() -> None:
    ap = argparse.ArgumentParser(prog="drama", description="AI 短剧创作工作台")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--slug", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--platform", default="TikTok")
    p.add_argument("--ep", type=int, default=20)
    p.add_argument("--sec", type=int, default=110)
    p.add_argument("--style-lock", default=DEFAULT_STYLE_LOCK)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("import-bible")
    p.set_defaults(func=cmd_import_bible)

    p = sub.add_parser("parse")
    p.add_argument("--ep", type=int, required=True)
    p.add_argument("--type", default="script", choices=["script", "storyboard"])
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("check")
    p.add_argument("--ep", type=int, required=True)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("convert")
    p.add_argument("--ep", type=int, required=True)
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("reindex")
    p.set_defaults(func=cmd_reindex)

    p = sub.add_parser("report")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("set-anchor")
    p.add_argument("--code", required=True)
    p.add_argument("--anchor", required=True)
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_set_anchor)

    p = sub.add_parser("gen")
    p.add_argument("--engine", default="runninghub")
    p.add_argument("--ep", type=int, required=True)
    p.add_argument("--shot", default="")
    p.set_defaults(func=cmd_gen)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
