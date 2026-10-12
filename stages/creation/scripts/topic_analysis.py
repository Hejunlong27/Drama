#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""topic_analysis.py —— 第③部分「选题定位」之 A：候选生成与评分（确定性评分引擎）

职责（**只做打分与交叉，不替你想选题**）
----------------------------------------
对应交付的第三部分：**明确目标受众画像、题材方向与差异化卖点。**
把 LLM（按 prompts/s2-topic-positioning.md）产出的**原始候选池** `candidates.raw.json`，
用**确定性算法**加工成可解释、可比较、不撞车的选题地图，并为每个候选萃取**定位三要素**：

1. **七维加权评分** → 加权总分 → S/A/B/C/D 评级；`score_basis` 逐维必填（防「感觉不错给 8 分」）。
2. **合规一票否决**：合规安全 ≤ 3 → 强制 D 并置顶风险。
3. **三维交叉比对**：候选 `freshness_evidence` 回指市场记录（①的榜单/对标剧/趋势）。
4. **新鲜度校验**：至少一条证据落在**最近 2 个月**窗口内，否则降级。
5. **差异化去重**：单赛道占比 > 40% 判定撞车，超额者降级。
6. **定位三要素**：`positioning = {受众画像, 题材方向, 差异化卖点[]}`，供第③部分 B（挑选）落成定位卡。

输入：`creation/market/market-data.json` + `creation/brief.json`
     + `creation/01-breakdown/breakdown.json`（②的拆解结论，用于定位校准；可选）
     + `creation/02-positioning/candidates.raw.json`
输出：`creation/02-positioning/candidates.json` + `candidates.md`

命令
----
    python topic_analysis.py score                打分+交叉+去重（需先备好 raw 与市场数据）
    python topic_analysis.py score --dry          只预览，不落盘
    python topic_analysis.py scaffold             写一份 candidates.raw.json 模板（供 LLM 填写）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _common as C  # noqa: E402
import market_data as MD  # noqa: E402

# ---- 评分模型（口径见 references/scoring-model.md） -------------------------
DIMS = [
    ("market_heat", "市场热度", 0.20),
    ("trend_freshness", "趋势新鲜度", 0.15),
    ("audience_fit", "受众契合", 0.15),
    ("platform_fit", "平台适配", 0.12),
    ("hook_strength", "钩子强度", 0.15),
    ("differentiation", "差异化", 0.13),
    ("compliance", "合规安全", 0.10),
]
DIM_KEYS = [d[0] for d in DIMS]
GRADES = [(8.5, "S"), (7.5, "A"), (6.5, "B"), (5.5, "C"), (-1.0, "D")]
VETO_DIM, VETO_MAX = "compliance", 3
MAX_TRACK_RATIO = 0.40
MIN_CANDIDATES = 8       # 低于此数只给「候选池」，不做 Top3
FRESH_WINDOW_DAYS = 60   # 证据须落在最近 2 个月内

REQUIRED_TOP = ["id", "name_zh", "logline", "conflict", "track", "audience",
                "hook_formula", "differentiation_anchor", "selling_points", "freshness_evidence"]

CROSS_LABELS = {
    frozenset({"hotspot", "trend"}): "热点 × 趋势",
    frozenset({"preference", "trend"}): "偏好 × 趋势",
    frozenset({"hotspot", "preference"}): "热点 × 偏好",
}


def _grade(total: float) -> str:
    for cut, g in GRADES:
        if total >= cut:
            return g
    return "D"


def _refs_by_id(md: dict) -> dict:
    """市场数据里所有可被引用的记录：id -> {'dim','date','label'}。"""
    out = {}
    for h in md.get("hits", []) or []:
        out[str(h.get("id"))] = {"dim": "hotspot", "date": C.parse_date(h.get("added")),
                                 "label": "对标剧 %s" % (h.get("title_zh") or h.get("title"))}
    for r in md.get("rankings", []) or []:
        out[str(r.get("id"))] = {"dim": "hotspot", "date": C.parse_date(r.get("date")),
                                 "label": "榜单 %s#%s" % (r.get("platform"), r.get("rank"))}
    for t in md.get("trends", []) or []:
        out[str(t.get("id"))] = {"dim": t.get("dim") or "trend",
                                 "date": C.parse_date(t.get("date")),
                                 "label": "趋势 %s" % t.get("topic")}
    for idx, note in enumerate(md.get("compliance_notes", []) or [], 1):
        out["C%02d" % idx] = {"dim": "compliance", "date": C.parse_date(note.get("date")),
                              "label": "合规 %s" % note.get("note")}
    return out


def cross_analysis(cand: dict, md: dict, today: _dt.date) -> dict:
    """三维交叉：候选声明的证据 id 回指市场记录 → 命中的维度集合与交叉标签。"""
    refs = _refs_by_id(md)
    support: set[str] = set()
    fresh_refs: list[str] = []
    stale_refs: list[str] = []
    unknown: list[str] = []
    for r in cand.get("freshness_evidence", []) or []:
        key = str(r)
        info = refs.get(key)
        if not info:
            unknown.append(key)
            continue
        support.add(info["dim"])
        d = info["date"]
        if d and (today - d).days <= FRESH_WINDOW_DAYS:
            fresh_refs.append(key)
        else:
            stale_refs.append(key)
    cross = [lbl for dims, lbl in CROSS_LABELS.items() if dims <= support]
    triple = {"hotspot", "preference", "trend"} <= support
    return {
        "support_dims": sorted(support),
        "cross": cross + (["三维交点（高置信）"] if triple else []),
        "triple": triple,
        "fresh_refs": fresh_refs, "stale_refs": stale_refs, "unknown_refs": unknown,
        "fresh_ok": bool(fresh_refs),
    }


def evaluate(cand: dict, md: dict, today: _dt.date, brief: dict | None = None,
             breakdown: dict | None = None) -> tuple[dict, list[str]]:
    """对一个候选打分。返回 (增强后的候选, 错误列表)。"""
    errs: list[str] = []
    cid = str(cand.get("id", "?"))
    for k in REQUIRED_TOP:
        if not cand.get(k):
            errs.append("%s 缺字段 `%s`" % (cid, k))
    scores = cand.get("scores") or {}
    basis = cand.get("score_basis") or {}
    missing_sc = [k for k in DIM_KEYS if k not in scores]
    missing_bs = [k for k in DIM_KEYS if not basis.get(k)]
    if missing_sc:
        errs.append("%s scores 缺维度：%s" % (cid, ",".join(missing_sc)))
    if missing_bs:
        errs.append("%s score_basis 缺依据（硬约束：每维必须给依据）：%s" % (cid, ",".join(missing_bs)))

    total = 0.0
    detail = {}
    for key, _label, w in DIMS:
        try:
            v = float(scores.get(key))
        except (TypeError, ValueError):
            v = 0.0
        v = max(0.0, min(10.0, v))
        total += w * v
        detail[key] = {"score": v, "weight": w, "basis": basis.get(key, "")}
    total = round(total, 2)

    veto = detail[VETO_DIM]["score"] <= VETO_MAX
    grade = "D" if veto else _grade(total)
    cross = cross_analysis(cand, md, today)
    if not cross["fresh_ok"]:
        errs.append("%s 新鲜度不足：证据未落在最近 %d 天内（其余证据 %s）"
                    % (cid, FRESH_WINDOW_DAYS, cross["stale_refs"] or cross["unknown_refs"] or "无"))

    out = dict(cand)
    out.update({"total": total, "grade": grade, "veto": veto,
                "dim_detail": detail, "cross": cross,
                "positioning": build_positioning(cand, brief, breakdown),
                "veto_reason": ("合规安全 %s ≤ %d，一票否决" % (detail[VETO_DIM]["score"], VETO_MAX)) if veto else "",
                "status": "vetoed" if veto else ("error" if errs else "kept")})
    return out, errs


def build_positioning(cand: dict, brief: dict | None, breakdown: dict | None) -> dict:
    """第③部分核心：为一个候选萃取**定位三要素**（受众画像 / 题材方向 / 差异化卖点）。"""
    brief = brief or {}
    bd = breakdown or {}
    # ② 拆解里的内容偏好：按赛道匹配对应市场的偏好与套路
    track = str(cand.get("track") or "")
    matched_prefs = []
    for p in bd.get("content_preferences", []) or []:
        if any(track and (track in str(t) or str(t) in track) for t in (p.get("ranking_tracks") or [])) \
                or any(track and (track in str(g) or str(g) in track) for g in (p.get("top_genres") or [])):
            matched_prefs.append({"market": p.get("market"), "is_spanish": p.get("is_spanish"),
                                  "top_tropes": p.get("top_tropes")})
    hot_tropes = [t["name"] for t in (bd.get("tropes") or []) if t.get("count", 0) >= 2][:4]
    return {
        "audience_profile": {
            "audience": cand.get("audience") or brief.get("audience"),
            "platform": cand.get("platform") or brief.get("platform"),
            "market": brief.get("market"),
            "age_gender": brief.get("audience"),
        },
        "genre_direction": track,
        "differentiation_selling_points": list(cand.get("selling_points") or [])
        + ([cand["differentiation_anchor"]] if cand.get("differentiation_anchor") else []),
        "hook_formula": list(cand.get("hook_formula") or []),
        "market_preference_evidence": matched_prefs,
        "market_hot_tropes": hot_tropes,
    }


def dedup(cands: list[dict]) -> list[str]:
    """同批去重：单赛道占比 > MAX_TRACK_RATIO 时，超额的低分候选降级为 demoted。"""
    notes: list[str] = []
    kept = [c for c in cands if c.get("status") == "kept"]
    n = len(kept)
    if n == 0:
        return notes
    by_track: dict[str, list[dict]] = {}
    for c in kept:
        by_track.setdefault(str(c.get("track", "?")), []).append(c)
    limit = max(1, int(n * MAX_TRACK_RATIO))
    for track, group in by_track.items():
        if len(group) <= limit:
            continue
        group.sort(key=lambda x: -x.get("total", 0))
        for c in group[limit:]:
            c["status"] = "demoted"
            c["demote_reason"] = "赛道「%s」占比 %d/%d 超 %.0f%%，按分降级" % (
                track, len(group), n, MAX_TRACK_RATIO * 100)
        notes.append("赛道「%s」%d 个候选超占比上限 %d，降级 %d 个"
                     % (track, len(group), limit, len(group) - limit))
    return notes


def run(ws: pathlib.Path, dry: bool = False) -> int:
    today = C.today()
    md = C.read_json(C.pdir(ws, C.MARKET_FILE)) or {}
    breakdown = C.read_json(C.pdir(ws, C.BREAKDOWN)) or {}
    cov = MD.check(ws)
    print(C.hr())
    print("③ 选题定位 · A（七维评分 + 三维交叉 + 差异化去重 + 定位三要素）")
    print(C.hr())
    if not cov["ok"]:
        print("[FAIL] 市场数据覆盖度不达标，拒绝执行（近 2 个月为硬约束）：")
        for e in cov["errors"]:
            print("   - %s" % e)
        print("   请先：python market_data.py queries → 检索 → 回填 → market_data.py check")
        return 2
    print("[OK] 市场数据覆盖度通过：%s（对标剧 %d / 榜单 %d / 趋势 %d / 近30天 %d）"
          % (cov["coverage"].get("window_to"), cov["coverage"].get("hits", 0),
             cov["coverage"].get("rankings", 0), cov["coverage"].get("trends", 0),
             cov["coverage"].get("recent_30d", 0)))
    if breakdown:
        print("[OK] 已接入「② 爆款拆解」：套路 %d 类 / 爆点 %d 类（用于定位校准）"
              % (len(breakdown.get("tropes") or []), len(breakdown.get("boom_elements") or [])))
    else:
        print("[WARN] 未找到 ② 拆解结论（%s）：定位将缺少市场偏好校准" % C.pdir(ws, C.BREAKDOWN))

    raw = C.read_json(C.pdir(ws, C.RAW_CANDIDATES))
    if not isinstance(raw, list) or not raw:
        print("[FAIL] 缺少原始候选池：%s" % C.pdir(ws, C.RAW_CANDIDATES))
        print("   下一步：python topic_analysis.py scaffold → 按 prompts/s2-topic-positioning.md 填候选")
        return 2

    brief = C.read_json(C.pdir(ws, C.BRIEF)) or {}
    all_errs: list[str] = []
    scored: list[dict] = []
    for c in raw:
        out, errs = evaluate(c, md, today, brief, breakdown)
        scored.append(out)
        all_errs += errs

    if len(scored) < MIN_CANDIDATES:
        all_errs.append("候选数 %d < 下限 %d：交叉做得不够，建议补足至 8-12 个"
                        % (len(scored), MIN_CANDIDATES))

    notes = dedup(scored)
    scored.sort(key=lambda x: (-(0 if x.get("veto") else 1), -x.get("total", 0)))

    counts = {"total": len(scored),
              "kept": sum(1 for c in scored if c["status"] == "kept"),
              "vetoed": sum(1 for c in scored if c["status"] == "vetoed"),
              "demoted": sum(1 for c in scored if c["status"] == "demoted"),
              "error": sum(1 for c in scored if c["status"] == "error"),
              "triple": sum(1 for c in scored if c["cross"]["triple"])}
    out_obj = {
        "generated_at": C.now_iso(),
        "module": "S2a-topic-analysis", "schema_version": 1,
        "market_window": {k: cov["coverage"].get(k) for k in
                          ("market", "window_from", "window_to", "span_days", "freshness_days")},
        "breakdown_used": bool(breakdown),
        "brief": brief, "counts": counts, "candidates": scored,
        "diagnostics": {"errors": all_errs, "dedup_notes": notes},
    }

    for c in scored:
        flag = {"vetoed": "X", "demoted": "v", "error": "!", "kept": " "}[c["status"]]
        print("  [%s] %-9s %-10s 总分%5.2f  %s%s"
              % (c["grade"], c.get("id"), str(c.get("track"))[:10], c.get("total", 0),
                 ("[否决] " if c.get("veto") else "") + str(c.get("name_zh"))[:26],
                 "  "+flag if flag.strip() else ""))
    if all_errs:
        print("-" * 74)
        print("诊断（%d 条，硬约束违规会导致候选不计入可选项）：" % len(all_errs))
        for e in all_errs[:20]:
            print("   - %s" % e)
    print("-" * 74)
    print("统计：候选 %(total)d | 有效 %(kept)d | 否决 %(vetoed)d | 降级 %(demoted)d | 三维交点 %(triple)d"
          % counts)

    if dry:
        print("[dry] 未落盘。")
        return 0
    p1 = C.write_json(C.pdir(ws, C.CANDIDATES), out_obj)
    p2 = C.write_text(C.pdir(ws, "creation/02-positioning/candidates.md"), render_md(out_obj))
    print("\n[OK] 已写出：\n     %s\n     %s" % (p1, p2))
    print("     下一步：python hit_selection.py select --top 3（敲定定位卡）")
    return 0


def render_md(obj: dict) -> str:
    c = obj["counts"]
    L = ["# ③ 选题定位 · A 候选池（%s）" % obj["generated_at"][:19], ""]
    L += ["- 市场窗口：%s ~ %s" % (obj["market_window"].get("window_from"),
                                  obj["market_window"].get("window_to")),
          "- 已接入② 拆解：%s" % ("是" if obj.get("breakdown_used") else "否"),
          "- 统计：候选 %(total)d / 有效 %(kept)d / 否决 %(vetoed)d / 降级 %(demoted)d / 三维交点 %(triple)d" % c,
          ""]
    L += ["## 候选总表", "",
          "| 评级 | ID | 题材命名 | 主赛道 | 总分 | 三维交叉 | 状态 |", "|---|---|---|---|---|---|---|"]
    for x in obj["candidates"]:
        L.append("| %s | %s | %s | %s | %.2f | %s | %s |"
                 % (x["grade"], x.get("id"), x.get("name_zh"), x.get("track"),
                    x.get("total", 0), "/".join(x["cross"]["cross"]) or "-", x["status"]))
    L += ["", "## Top3 定位卡", ""]
    top = [x for x in obj["candidates"] if x["status"] == "kept"][:3]
    for i, x in enumerate(top, 1):
        pos = x.get("positioning") or {}
        ap = pos.get("audience_profile") or {}
        L += ["### %d. %s（%s，总分 %.2f）" % (i, x.get("name_zh"), x["grade"], x.get("total", 0)),
              "",
              "- **logline**：%s" % x.get("logline"),
              "- **核心冲突**：%s" % x.get("conflict"),
              "- **受众画像**：%s ｜ %s ｜ 平台 %s" % (ap.get("audience"), ap.get("age_gender"), ap.get("platform")),
              "- **题材方向**：%s" % pos.get("genre_direction"),
              "- **差异化卖点**：%s" % "；".join(pos.get("differentiation_selling_points") or []) or "-",
              "- **爆款公式**：%s" % "、".join(x.get("hook_formula", []) or []),
              "- **三维交叉**：%s（证据 %s）" % ("、".join(x["cross"]["cross"]) or "-",
                                                ", ".join(x["cross"]["fresh_refs"])),
              "- **市场偏好校准**：%s" % ("、".join(pos.get("market_hot_tropes") or []) or "无②数据"),
              "- **七维明细**：%s" % "；".join(
                  "%s %s(%s)" % (x["dim_detail"][k]["score"], k, x["dim_detail"][k]["basis"][:24])
                  for k in DIM_KEYS),
              ""]
    if obj["diagnostics"]["errors"]:
        L += ["## 诊断", ""]
        L += ["- %s" % e for e in obj["diagnostics"]["errors"]]
    return "\n".join(L)


RAW_TEMPLATE = [
    {
        "id": "C01", "name_zh": "<身份+冲突 直给式命名>", "name_es": "<西语名>",
        "logline": "<含主角身份/困局/钩子，≤40字>",
        "conflict": "<谁 vs 谁 vs 什么>", "track": "<主赛道>",
        "audience": "<目标受众>", "platform": "<主投平台>",
        "scores": {k: 7 for k in DIM_KEYS},
        "score_basis": {k: "<该维度给分的具体依据：哪部剧/哪个榜单/哪条政策>" for k in DIM_KEYS},
        "hook_formula": ["<爽点1>", "<爽点2>"],
        "differentiation_anchor": "<与同赛道头部剧的具体区别>",
        "selling_points": ["<差异化卖点1>", "<差异化卖点2>", "<差异化卖点3>"],
        "freshness_evidence": ["T01", "H01"],
        "production_path": {"cost_tier": "低", "cycle": "<周期>", "ai_fit": "高", "series": True},
        "risks": ["<风险/合规红线>"],
    }
]


def cmd_scaffold(a) -> int:
    ws = C.resolve_ws(a.workspace)
    p = C.write_json(C.pdir(ws, C.RAW_CANDIDATES), RAW_TEMPLATE * 1)
    print("[OK] 已写出原始候选池模板：%s" % p)
    print("     按 prompts/s2-topic-positioning.md 生成 8-12 个候选后，再跑 `score`。")
    print("     硬约束：score_basis 七维逐维给依据；selling_points/audience/track 必填。")
    return 0


def main() -> int:
    C.harden_stdio()
    ap = argparse.ArgumentParser(description="③ 选题定位·A：七维评分 + 三维交叉 + 去重 + 定位三要素")
    ap.add_argument("--workspace", default=None)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("score", help="打分（需先备好市场数据与 raw 候选）")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=lambda a: run(C.resolve_ws(a.workspace), dry=a.dry))
    p = sub.add_parser("scaffold", help="写 raw 候选模板")
    p.set_defaults(fn=cmd_scaffold)
    a = ap.parse_args()
    if not getattr(a, "fn", None):
        return run(C.resolve_ws(a.workspace))
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
