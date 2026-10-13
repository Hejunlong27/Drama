#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""creation.py —— 西语 AI 漫剧「创作链路」总入口（四部分）

链路（各环节职责单一、产物落盘、可断点续跑）
--------------------------------------------
    ① 榜单调研与对比  market_data.py    多市场榜单 + 横向对比（西语重点）+ 覆盖度门禁
    ② 爆款拆解        hit_breakdown.py  题材类型/剧情套路/爆点元素/内容偏好/差异化趋势
    ③ 选题定位        topic_analysis.py 候选 + 七维评分 + 三维交叉（A）
                      hit_selection.py  多样性排期 + 对标匹配 → 定位卡（B）
    ④ 剧本改写        script_rewrite.py 源剧本 → N 集规划（默认 60）+ FFS 骨架 + 自检
    ── 交付 ──        tools/cli.py      init / import-bible / check / convert
    ── 执行 ──        stages/production/drama.py  模型门禁 → 资产 → 出片 → 字幕

本脚本只做三件事：
  1. `scaffold` —— 一条命令把一个西语漫剧项目**初始化到位**并**对接现有 agent 体系**：
     建目录、拷贝创作层工具链（tools/）、写 project.json、生成创作 brief、就位数据契约。
  2. 转发 —— `market/breakdown/topic/hit/script` 直接调用对应模块（不重复实现）。
  3. `status` —— 一屏看清四部分走到哪一步、还缺什么。

命令
----
    python creation.py scaffold --workspace "D:/剧/新剧" --slug mi-venganza --title "我的复仇有爪牙" \
        --platform ReelShort --ep 60 --sec 90
    python creation.py status
    python creation.py market queries | market check | market compare | market template --write
    python creation.py breakdown analyze
    python creation.py topic scaffold | topic score
    python creation.py hit select --top 3
    python creation.py script plan --episodes 60 | script build --ep 1 --emit | script lint --ep 1
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _common as C  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]   # .../SoloDrama
SCRIPTS = pathlib.Path(__file__).resolve().parent

BRIEF_TEMPLATE = {
    "slug": "<英文短名>", "title": "<中文剧名>",
    "market": "es-LATAM", "market_label": "西语（拉美 + 西班牙）",
    "platform": "<ReelShort|TikTok>", "form": "<AI漫剧|真人竖屏>",
    "audience": "<拉美女性 18-40>",
    "genre_directions": ["<女频逆袭>", "<狼人奇幻>"],
    "budget_tier": "<低|中|高>",
    "ep_total": 60, "ep_seconds": 90,
    "style_hint": "<可选：色卡/基调偏好>",
    "notes": "<其他约束，如：源剧本很颠，保留其核心爽点>",
}

# 四部分与产物（status 一屏依据；标签等宽以便列对齐）
STEPS = [
    ("① 调研对比", "creation/market/market-data.json"),
    ("② 爆款拆解", "creation/01-breakdown/breakdown.json"),
    ("③ 选题定位", "creation/02-positioning/positioning.json"),
    ("④ 剧本改写", "creation/03-script/blueprint.json"),
]


def _run(cmd: list[str], cwd: pathlib.Path | None = None) -> int:
    print("  $ %s" % " ".join(str(c) for c in cmd[2:]))
    rc = subprocess.call([str(c) for c in cmd], cwd=str(cwd) if cwd else None)
    return rc


def _py() -> str:
    return sys.executable or "python"


# ---------------------------------------------------------------------------
def cmd_scaffold(a) -> int:
    explicit = getattr(a, "workspace", None)
    ws = pathlib.Path(explicit).expanduser().resolve() if explicit else C.resolve_ws(None)
    ws = pathlib.Path(ws)
    ws.mkdir(parents=True, exist_ok=True)
    print(C.hr())
    print("西语 AI 漫剧 · 项目初始化（对接现有 agent 体系）")
    print(C.hr())
    print("  工作区：%s" % ws)

    # 1) 数据契约骨架（引擎侧约定：<ws>/video-pipeline）
    for d in ("video-pipeline/data", "video-pipeline/output", "video-pipeline/ledger"):
        (ws / d).mkdir(parents=True, exist_ok=True)

    # 2) 创作层目录 + brief
    for d in (C.MARKET_DIR, C.BREAKDOWN_DIR, C.POSITIONING_DIR, C.SCRIPT_DIR):
        (ws / d).mkdir(parents=True, exist_ok=True)
    brief = dict(BRIEF_TEMPLATE)
    brief.update({"slug": a.slug, "title": a.title, "platform": a.platform,
                  "ep_total": a.ep, "ep_seconds": a.sec})
    brief_path = C.write_json(ws / C.BRIEF, brief)
    print("  [OK] 创作 brief：%s" % brief_path)

    # 3) 收编创作层工具链 tools/（项目自带一份，与内容解耦）
    tools_dst = ws / "tools"
    tools_src = REPO_ROOT / "tools"
    if tools_src.is_dir():
        if tools_dst.exists():
            print("  [SKIP] tools/ 已存在，保持不动：%s" % tools_dst)
        else:
            shutil.copytree(tools_src, tools_dst)
            print("  [OK] 创作层工具链已就位：%s" % tools_dst)
    else:
        print("  [WARN] 仓库内未找到 tools/（%s），跳过" % tools_src)

    # 4) 调 tools/cli.py init（写 project.json + 目录树 + stylelock）
    if (tools_dst / "cli.py").is_file():
        style_lock = a.style_lock or ""
        cmd = [_py(), str(tools_dst / "cli.py"), "init", "--slug", a.slug, "--title", a.title,
               "--platform", a.platform, "--ep", str(a.ep), "--sec", str(a.sec)]
        if style_lock:
            cmd += ["--style-lock", style_lock]
        print("\n  ── 对接创作层工具链 ──")
        _run(cmd, cwd=ws)
    else:
        print("  [WARN] 未找到 tools/cli.py，project.json 未生成")

    # 5) 市场数据模板
    C.write_text(ws / C.MARKET_FILE, json.dumps(_market_template_json(), ensure_ascii=False, indent=2))
    print("\n  [OK] 市场数据模板：%s" % (ws / C.MARKET_FILE))

    print("\n" + "-" * 74)
    print("四部分流程（顺序执行）：")
    print("  0) 确认模型（前置条件）：python stages/production/scripts/drama.py models --ask")
    print("  ① 榜单调研与对比：creation.py market queries → 检索 → 回填 → market check / market compare")
    print("  ② 爆款拆解      ：creation.py breakdown analyze")
    print("  ③ 选题定位      ：creation.py topic scaffold（填 brief 与候选）→ topic score → hit select --top 3")
    print("  ④ 剧本改写      ：把源剧本放到 creation/03-script/source-script.md")
    print("                    → creation.py script plan --episodes 60 → build --ep 1 --emit → lint --ep 1")
    return 0


def _market_template_json() -> dict:
    import market_data as MD
    return MD.TEMPLATE


# ---------------------------------------------------------------------------
def cmd_status(a) -> int:
    ws = C.resolve_ws(getattr(a, "workspace", None))
    print(C.hr())
    print("西语 AI 漫剧 · 创作链路状态（四部分）")
    print(C.hr())
    print("  工作区：%s" % ws)
    for label, rel in STEPS:
        p = ws / rel
        print("  %-16s %s  %s" % (label, "[就绪]" if p.is_file() else "[缺失]", rel))
    mg = ws / "model-plan.json"
    print("  %-16s %s  model-plan.json" % ("模型门禁", "[已确认]" if mg.is_file() else "[未确认]"))
    src = ws / C.SOURCE_SCRIPT
    print("  %-16s %s  %s" % ("源剧本(很颠)", "[已放]" if src.is_file() else "[未放]", C.SOURCE_SCRIPT))
    # 关键计数
    md = C.read_json(ws / C.MARKET_FILE) or {}
    if md:
        print("\n  ① 市场数据：对标剧 %d / 榜单 %d / 市场 %d / 趋势 %d"
              % (len(md.get("hits") or []), len(md.get("rankings") or []),
                 len(md.get("markets") or []), len(md.get("trends") or [])))
    bd = C.read_json(ws / C.BREAKDOWN) or {}
    if bd:
        print("  ② 拆解    ：题材 %d 类 / 套路 %d 类 / 爆点 %d 类 / 差异化趋势 %d 条"
              % (len(bd.get("genre_distribution") or []), len(bd.get("tropes") or []),
                 len(bd.get("boom_elements") or []), len(bd.get("differentiation_trends") or [])))
    cand = C.read_json(ws / C.CANDIDATES) or {}
    if cand:
        print("  ③ 候选池  ：%s" % json.dumps(cand.get("counts", {}), ensure_ascii=False))
    sel = C.read_json(ws / C.POSITIONING) or {}
    if sel:
        print("  ③ 定位卡  ：%s" % "、".join(x.get("title", "?") for x in sel.get("selected", [])))
    bp = C.read_json(ws / C.BLUEPRINT) or {}
    if bp:
        print("  ④ 集数规划：%d 集（源剧本 %s）"
              % (bp.get("total_ep", 0), "已登记" if (bp.get("source_script") or {}).get("present") else "未提供"))
    print("-" * 74)
    missing = [rel for _, rel in STEPS if not (ws / rel).is_file()]
    if not missing:
        print("[OK] 四部分齐备。可进入交付：tools/cli.py check/convert → drama.py 门禁→资产→出片")
    else:
        print("待办：见上方 [缺失] 项；或跑 creation.py guide 看流程。")
    return 0


def cmd_guide(a) -> int:
    print(__doc__)
    return 0


# ---------------------------------------------------------------------------
def _forward(module: str, argv: list[str], ws: str | None) -> int:
    cmd = [_py(), str(SCRIPTS / module)]
    if ws:
        cmd += ["--workspace", ws]
    cmd += argv
    return _run(cmd)


def main() -> int:
    C.harden_stdio()
    ap = argparse.ArgumentParser(description="西语 AI 漫剧创作链路总入口（①榜单调研 → ②爆款拆解 → ③选题定位 → ④剧本改写）")
    ap.add_argument("--workspace", default=None, help="工作区（也可写在子命令之后）")
    # 子命令上的 --workspace 用 SUPPRESS 缺省：只在显式给出时覆盖主命令的值，避免互相清空
    wp = argparse.ArgumentParser(add_help=False)
    wp.add_argument("--workspace", default=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("scaffold", help="初始化项目并对接现有 agent 体系", parents=[wp])
    p.add_argument("--slug", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--platform", default="ReelShort")
    p.add_argument("--ep", type=int, default=60, help="总集数（④剧本改写规划用，默认 60）")
    p.add_argument("--sec", type=int, default=90)
    p.add_argument("--style-lock", default=None)
    p.set_defaults(fn=cmd_scaffold)

    p = sub.add_parser("status", help="链路状态一屏", parents=[wp])
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("guide", help="打印流程说明")
    p.set_defaults(fn=cmd_guide)

    # 转发型子命令：--workspace 请写在主命令位置（`creation.py --workspace X market check`）
    for name, module in (("market", "market_data.py"), ("breakdown", "hit_breakdown.py"),
                         ("topic", "topic_analysis.py"), ("hit", "hit_selection.py"),
                         ("script", "script_rewrite.py")):
        q = sub.add_parser(name, help="转发到 %s" % module)
        q.add_argument("rest", nargs=argparse.REMAINDER)
        q.set_defaults(_module=module)

    a = ap.parse_args()
    if getattr(a, "_module", None):
        return _forward(a._module, a.rest, getattr(a, "workspace", None))
    if not getattr(a, "fn", None):
        return cmd_guide(a)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
