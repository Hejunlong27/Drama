#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""hit_selection.py —— 第③部分「选题定位」之 B：敲定定位卡

职责（**只做挑选与定位卡，不新增选题**）
----------------------------------------
输入第③部分 A（`topic_analysis.py`）产出的候选池，输出**要立项的 N 个「定位卡」**，
完成交付第三部分「明确目标受众画像、题材方向与差异化卖点」：

1. **否决与降级隔离**：`vetoed`（合规一票否决）/ `error`（硬约束不合格）不进候选；
   `demoted` 不占正式席位。
2. **多样性排期**：按总分贪心选取，**同一主赛道在 Top-N 中最多 1 个**（避免「一批选题长得像」）。
3. **对标匹配**：把选中选题与①的爆款对标剧做赛道/标签匹配 —— 「它踩中了哪几部爆款已验证的结构」。
4. **拆解校准**：与②的拆解结论（高频套路/爆点）对齐，标注「顺势」还是「差异化切口」。
5. **定位卡**：输出**受众画像 / 题材方向 / 差异化卖点**三要素 + 立项建议 + 给④的差异化红线。

输入：`creation/02-positioning/candidates.json` + `creation/market/market-data.json`
     + `creation/01-breakdown/breakdown.json`（可选）
输出：`creation/02-positioning/positioning.json` + `positioning.md`

命令：python hit_selection.py select [--top 3] [--source-json 路径] [--dry]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _common as C  # noqa: E402

DEFAULT_TOP = 3


def _tokens(*texts) -> set[str]:
    out: set[str] = set()
    for t in texts:
        for part in str(t or "").replace("/", " ").replace("、", " ").replace(",", " ").split():
            p = part.strip().lower()
            if len(p) >= 2:
                out.add(p)
    return out


def match_hits(cand: dict, md: dict, limit: int = 3) -> list[dict]:
    """与①的爆款对标剧做赛道/标签匹配。"""
    c_track = str(cand.get("track") or "").lower()
    c_tokens = _tokens(cand.get("track"), cand.get("name_zh"), cand.get("logline"),
                       " ".join(cand.get("hook_formula", []) or []))
    scored = []
    for h in md.get("hits", []) or []:
        hit_track = str(h.get("track") or "").lower()
        hit_tags = [str(t).lower() for t in (h.get("tags") or [])]
        overlap: list[str] = []
        if c_track and (c_track in hit_track or hit_track in c_track):
            overlap.append("赛道")
        for tg in hit_tags:
            if tg and (tg in c_tokens or any(tg in tk for tk in c_tokens)):
                overlap.append(tg)
        if overlap:
            scored.append({"id": h.get("id"), "title": h.get("title_zh") or h.get("title"),
                           "platform": h.get("platform"), "overlap": sorted(set(overlap)),
                           "_n": len(set(overlap))})
    scored.sort(key=lambda x: -x["_n"])
    for s in scored:
        s.pop("_n", None)
    return scored[:limit]


def align_breakdown(cand: dict, bd: dict) -> dict:
    """与②拆解结论对齐：命中哪些高频套路/爆点 → 顺势 or 差异化切口。"""
    track = str(cand.get("track") or "")
    text = " ".join([track, " ".join(cand.get("hook_formula", []) or []),
                     str(cand.get("logline") or "")]).lower()
    hot_tropes, tail_tropes = [], []
    for t in bd.get("tropes", []) or []:
        name = str(t.get("name"))
        keys = [name] + [x for x in (t.get("examples") or [])]
        hit = any(str(k).lower() in text for k in keys)
        if not hit:
            continue
        (hot_tropes if t.get("count", 0) >= 2 else tail_tropes).append(name)
    return {"aligned_tropes": hot_tropes, "blue_ocean_tropes": tail_tropes,
            "stance": ("顺势（踩中已验证套路）" if hot_tropes else "差异化（走长尾切口）")}


def select(ws: pathlib.Path, top_n: int, src: str | None = None, dry: bool = False) -> int:
    src_rel = src or C.CANDIDATES
    obj = C.read_json(C.pdir(ws, src_rel))
    if not isinstance(obj, dict) or not obj.get("candidates"):
        print("[FAIL] 找不到候选池：%s（先跑 topic_analysis.py score）" % C.pdir(ws, src_rel))
        return 2
    md = C.read_json(C.pdir(ws, C.MARKET_FILE)) or {}
    bd = C.read_json(C.pdir(ws, C.BREAKDOWN)) or {}
    brief = C.read_json(C.pdir(ws, C.BRIEF)) or {}
    cands = obj["candidates"]

    print(C.hr())
    print("③ 选题定位 · B（多样性排期 + 对标匹配 + 定位卡）")
    print(C.hr())

    pool = [c for c in cands if c.get("status") == "kept"]
    pool.sort(key=lambda x: -x.get("total", 0))
    rejected: list[dict] = []

    selected: list[dict] = []
    used_tracks: set[str] = set()
    for c in pool:                                    # 第一轮：每赛道最多 1 个
        if len(selected) >= top_n:
            break
        tr = str(c.get("track"))
        if tr in used_tracks:
            continue
        used_tracks.add(tr)
        selected.append(c)
    if len(selected) < top_n:                         # 第二轮：赛道不足时放宽
        chosen = {id(x) for x in selected}
        for c in pool:
            if len(selected) >= top_n:
                break
            if id(c) not in chosen:
                selected.append(c)

    sel_ids = {id(x) for x in selected}
    for c in pool:
        if id(c) not in sel_ids:
            rejected.append({"id": c.get("id"), "reason": "总分排序未进 Top-%d，或同赛道已占位" % top_n})
    for c in cands:
        if c.get("status") == "vetoed":
            rejected.append({"id": c.get("id"), "reason": c.get("veto_reason") or "合规一票否决"})
        elif c.get("status") == "error":
            rejected.append({"id": c.get("id"), "reason": "硬约束不达标（评分依据/新鲜度缺失）"})
        elif c.get("status") == "demoted":
            rejected.append({"id": c.get("id"), "reason": c.get("demote_reason") or "赛道占比超限被降级"})

    out_selected = []
    for i, c in enumerate(selected, 1):
        mh = match_hits(c, md, 3)
        ab = align_breakdown(c, bd)
        pos = c.get("positioning") or {}
        card = {
            "rank": i,
            "title": c.get("name_zh"), "title_es": c.get("name_es"),
            "logline": c.get("logline"), "conflict": c.get("conflict"),
            "audience_profile": pos.get("audience_profile") or {"audience": c.get("audience"),
                                                                "platform": c.get("platform")},
            "genre_direction": c.get("track"),
            "differentiation_selling_points": pos.get("differentiation_selling_points") or [],
            "hook_formula": c.get("hook_formula") or [],
            "match_hits": mh, "breakdown_alignment": ab,
            "grade": c.get("grade"), "total": c.get("total"),
            "why": ("总分 %.2f（%s 级）；三维交叉：%s；对标命中：%s；②校准：%s"
                    % (c.get("total", 0), c.get("grade"),
                       "、".join((c.get("cross") or {}).get("cross") or []) or "无",
                       "、".join(m["title"] for m in mh) or "无", ab["stance"])),
            "differentiation_redlines": [
                "不复刻任何对标剧的台词/镜序/角色名与专属设定，只借鉴结构",
                "不得与同批其它选题共用同一核心冲突结构",
                "差异化卖点必须可验证：%s" % (c.get("differentiation_anchor") or "（待补）"),
                "台词为口语西语（拉美通用），文化元素须可代入（家庭/阶层/都市/足球）",
            ],
        }
        out_selected.append(card)

    tracks = sorted({str(x["genre_direction"]) for x in out_selected})
    out_obj = {
        "generated_at": C.now_iso(), "module": "S2b-topic-positioning", "schema_version": 1,
        "source": src_rel, "market_window": obj.get("market_window"),
        "breakdown_used": bool(bd), "brief": brief,
        "top_n": top_n, "selected": out_selected, "rejected": rejected,
        "diversity": {"tracks": tracks, "distinct_tracks": len(tracks)},
    }

    for x in out_selected:
        print("  #%d %-9s %-10s 总分%5.2f %s"
              % (x["rank"], x.get("title"), str(x.get("genre_direction"))[:10], x.get("total", 0),
                 x["title"]))
        print("      受众：%s ｜ 差异化卖点：%s"
              % ((x["audience_profile"] or {}).get("audience") or "-",
                 "；".join(x["differentiation_selling_points"]) or "-"))
        print("      对标：%s ｜ ②校准：%s"
              % ("、".join(m["title"] for m in x["match_hits"]) or "无", x["breakdown_alignment"]["stance"]))
    if not out_selected:
        print("  [WARN] 没有可立项的候选（池内全部被否决或不合格）")
    print("-" * 74)
    print("定位建议：先做 #1 %s，其后备选 %s"
          % (out_selected[0]["title"] if out_selected else "-",
             "、".join(x["title"] for x in out_selected[1:]) or "无"))
    print("剔除 %d 个（否决/不合格/未入 Top-N）" % len(rejected))

    if dry:
        print("[dry] 未落盘。")
        return 0
    p1 = C.write_json(C.pdir(ws, C.POSITIONING), out_obj)
    p2 = C.write_text(C.pdir(ws, "creation/02-positioning/positioning.md"), render_md(out_obj))
    print("\n[OK] 已写出：\n     %s\n     %s" % (p1, p2))
    print("     下一步：把待改写剧本放到 creation/03-script/source-script.md → script_rewrite.py plan")
    return 0


def render_md(o: dict) -> str:
    L = ["# ③ 选题定位卡（%s）" % o["generated_at"][:19], "",
         "- 来源：%s　Top-N：%d　赛道数：%d　②拆解校准：%s"
         % (o["source"], o["top_n"], o["diversity"]["distinct_tracks"],
            "已接入" if o.get("breakdown_used") else "未接入"), "", "## 定位卡", ""]
    for x in o["selected"]:
        ap = x["audience_profile"] or {}
        L += ["### #%d %s（%s · 总分 %.2f）" % (x["rank"], x["title"], x["grade"], x["total"]), "",
              "- **logline**：%s" % x["logline"],
              "- **核心冲突**：%s" % x["conflict"],
              "- **① 受众画像**：受众 %s ｜ 平台 %s ｜ 市场 %s"
              % (ap.get("audience"), ap.get("platform"), ap.get("market")),
              "- **② 题材方向**：%s" % x["genre_direction"],
              "- **③ 差异化卖点**：%s" % "；".join(x["differentiation_selling_points"]) or "-",
              "- **爆款公式**：%s" % "、".join(x["hook_formula"]) or "-",
              "- **对标命中**：%s" % ("、".join("%s(%s)" % (m["title"], "/".join(m["overlap"]))
                                              for m in x["match_hits"]) or "无"),
              "- **② 拆解校准**：%s（顺势套路 %s / 蓝海切口 %s）"
              % (x["breakdown_alignment"]["stance"],
                 "、".join(x["breakdown_alignment"]["aligned_tropes"]) or "-",
                 "、".join(x["breakdown_alignment"]["blue_ocean_tropes"]) or "-"),
              "- **给④的差异化红线**："] + ["  - %s" % r for r in x["differentiation_redlines"]] + [""]
    if o["rejected"]:
        L += ["## 剔除清单", "", "| ID | 原因 |", "|---|---|"]
        L += ["| %s | %s |" % (r["id"], r["reason"]) for r in o["rejected"]]
    return "\n".join(L)


def main() -> int:
    C.harden_stdio()
    ap = argparse.ArgumentParser(description="③ 选题定位·B：多样性排期 + 对标匹配 + 定位卡")
    ap.add_argument("--workspace", default=None)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("select", help="从候选池敲定 Top-N 定位卡")
    p.add_argument("--top", type=int, default=DEFAULT_TOP)
    p.add_argument("--source-json", default=None)
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=lambda a: select(C.resolve_ws(a.workspace), a.top, a.source_json, a.dry))
    a = ap.parse_args()
    if not getattr(a, "fn", None):
        return select(C.resolve_ws(a.workspace), DEFAULT_TOP, None, False)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
