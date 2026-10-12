#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""hit_breakdown.py —— 第②部分「爆款拆解」

职责（**只做拆解，不产出选题**）
--------------------------------
基于第①部分的市场数据，用**确定性算法**拆解出可复核的结构化结论：

1. **题材类型分布**：按对标剧的主赛道聚合，给出数量与占比。
2. **剧情套路库**：把标签/赛道/剧名映射到一套**套路分类**（身份反转 / 血统觉醒 / 复仇打脸 …），
   统计频次与占比 —— 回答「西语市场在反复用哪些套路」。
3. **爆点元素频次**：把钩子/标签映射到**爆点元素**（身份揭穿 / 当众打脸 / 血月觉醒 …），
   给出高频爆点。
4. **榜单洞察**：按平台与题材聚合榜单条目，回答「哪个平台押什么题材」。
5. **内容偏好**：按市场聚合榜单赛道 → 映射套路，给出各市场（含西语重点）的题材偏好。
6. **差异化趋势**：只出现 1 次（长尾）或仅见于近期趋势的套路 → 标记为**可能的蓝海切口**。

输入：`<workspace>/creation/market/market-data.json`
输出：`<workspace>/creation/01-breakdown/breakdown.json` + `breakdown.md`

命令
----
    python hit_breakdown.py analyze            拆解并落盘
    python hit_breakdown.py analyze --dry      只预览
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _common as C  # noqa: E402
import market_data as MD  # noqa: E402

# ---- 套路库（trope）与爆点库（boom）：拆解的「词典」，可扩充 ----------------
TROPE_LIBRARY: dict[str, list[str]] = {
    "身份反转": ["identity", "reversal", "hidden identity", "secret identity", "身份", "反转", "伪装"],
    "血统觉醒": ["werewolf", "vampire", "alpha", "bloodline", "beast", "curse", "wolf",
              "狼人", "吸血鬼", "血统", "兽", "诅咒"],
    "复仇打脸": ["revenge", "betrayal", "payback", "vengeance", "复仇", "背叛", "打脸", "逆袭"],
    "契约关系": ["contract", "arranged marriage", "marriage of convenience", "契约", "先婚后爱", "替嫁"],
    "重生穿越": ["time travel", "regression", "rebirth", "second chance", "穿越", "重生", "回到"],
    "豪门霸总": ["ceo", "billionaire", "tycoon", "heiress", "霸总", "豪门", "总裁", "权势"],
    "失忆替身": ["amnesia", "substitute", "stand-in", "失忆", "替身"],
    "家族伦理": ["family", "inheritance", "legacy", "家族", "继承", "伦理"],
    "阶层落差": ["class", "cinderella", "poor", "poverty", "阶层", "灰姑娘", "草根"],
    "超自然": ["supernatural", "magic", "witch", "demon", "fantasy", "超自然", "魔法", "奇幻"],
    "悬疑惊悚": ["mystery", "thriller", "murder", "悬疑", "惊悚", "凶案"],
    "禁忌之恋": ["forbidden", "taboo", "affair", "禁忌", "不伦"],
}

BOOM_LIBRARY: dict[str, list[str]] = {
    "身份揭穿": ["identity revealed", "reveal", "身份", "揭穿", "认出"],
    "当众打脸": ["public", "humiliat", "打脸", "当众", "羞辱", "碾压"],
    "觉醒变强": ["blood moon", "awaken", "血月", "觉醒", "变身", "升级"],
    "死亡反转": ["death", "survive", "死于", "复活", "没死", "绝境"],
    "夺权阴谋": ["coup", "betray", "usurp", "夺权", "篡位", "阴谋", "背叛"],
    "旧爱重逢": ["reunion", "lost love", "重逢", "旧爱", "破镜"],
    "丑闻曝光": ["exposed", "scandal", "曝光", "丑闻"],
    "代价抉择": ["sacrifice", "choice", "牺牲", "抉择", "取舍"],
}


def _blob(*fields) -> str:
    parts: list[str] = []
    for f in fields:
        if isinstance(f, (list, tuple, set)):
            parts += [str(x) for x in f]
        elif f:
            parts.append(str(f))
    return " ".join(parts).lower()


def _count_library(lib: dict, items: list[dict], fields) -> list[dict]:
    total = len(items) or 1
    rows = []
    for name, keys in lib.items():
        ex, hit = [], 0
        for it in items:
            blob = _blob(*[it.get(f) for f in fields])
            if any(k.lower() in blob for k in keys):
                hit += 1
                ex.append(it.get("title_zh") or it.get("title") or it.get("id"))
        rows.append({"name": name, "count": hit, "share_pct": C.pct(hit, total),
                     "examples": [e for e in ex if e][:4]})
    rows.sort(key=lambda r: (-r["count"], r["name"]))
    return rows


def analyze(ws: pathlib.Path, dry: bool = False) -> int:
    md = C.read_json(C.pdir(ws, C.MARKET_FILE))
    cov = MD.check(ws)
    print(C.hr())
    print("② 爆款拆解（题材分布 / 剧情套路 / 爆点元素 / 偏好 / 差异化趋势）")
    print(C.hr())
    if not isinstance(md, dict) or not md:
        print("[FAIL] 缺少市场数据：%s（先跑 market_data.py template --write 并回填）"
              % C.pdir(ws, C.MARKET_FILE))
        return 2
    if not cov["ok"]:
        print("[FAIL] 市场数据覆盖度不达标，拒绝拆解（第①部分未完成）：")
        for e in cov["errors"]:
            print("   - %s" % e)
        return 2

    hits = md.get("hits") or []
    rankings = md.get("rankings") or []
    markets = md.get("markets") or []

    # 1) 题材类型分布
    genre_counter: dict[str, list] = {}
    for h in hits:
        g = str(h.get("track") or "未分类")
        genre_counter.setdefault(g, []).append(h.get("title_zh") or h.get("title"))
    genre_distribution = sorted(
        [{"genre": g, "count": len(v), "share_pct": C.pct(len(v), len(hits) or 1),
          "examples": [x for x in v if x][:4]} for g, v in genre_counter.items()],
        key=lambda r: (-r["count"], r["genre"]))

    # 2) 剧情套路
    tropes = _count_library(TROPE_LIBRARY, hits, ("track", "tags", "title", "title_zh", "hook"))

    # 3) 爆点元素
    booms = _count_library(BOOM_LIBRARY, hits, ("hook", "tags", "title", "title_zh"))

    # 4) 榜单洞察
    by_platform: dict[str, int] = {}
    by_track: dict[str, int] = {}
    for r in rankings:
        by_platform[str(r.get("platform"))] = by_platform.get(str(r.get("platform")), 0) + 1
        by_track[str(r.get("track"))] = by_track.get(str(r.get("track")), 0) + 1
    ranking_insights = {
        "total": len(rankings),
        "by_platform": [{"platform": k, "count": v} for k, v in
                        sorted(by_platform.items(), key=lambda x: -x[1])],
        "by_track": [{"track": k, "count": v} for k, v in
                     sorted(by_track.items(), key=lambda x: -x[1])],
    }

    # 5) 内容偏好（按市场；西语置顶）
    prefs = []
    for m in markets:
        mid = m.get("id")
        mname = m.get("name") or mid
        tracks = [str(r.get("track")) for r in rankings
                  if str(r.get("market")) in (str(mid), str(mname))]
        local_tropes: dict[str, int] = {}
        for t in tracks:
            blob = t.lower()
            for name, keys in TROPE_LIBRARY.items():
                if any(k.lower() in blob for k in keys):
                    local_tropes[name] = local_tropes.get(name, 0) + 1
        prefs.append({
            "market": mname, "is_spanish": MD.is_spanish(m),
            "top_genres": m.get("top_genres") or [],
            "ranking_tracks": sorted(set(tracks)),
            "top_tropes": [k for k, _ in sorted(local_tropes.items(), key=lambda x: -x[1])][:3],
            "note": m.get("note"),
        })
    prefs.sort(key=lambda p: (0 if p["is_spanish"] else 1, p["market"]))

    # 6) 差异化趋势（长尾=只出现 1 次的套路；或仅见于趋势的新词）
    recent_topics = " ".join(str(t.get("topic", "")) for t in (md.get("trends") or [])).lower()
    diff = []
    for row in tropes:
        if row["count"] <= 1:
            diff.append({"trend": row["name"], "kind": "长尾套路（低频，可做差异化切口）",
                         "evidence": "对标剧中仅 %d 部命中" % row["count"],
                         "examples": row["examples"]})
    for row in booms:
        if row["count"] == 0:
            diff.append({"trend": row["name"], "kind": "未饱和爆点（对标剧尚未密集使用）",
                         "evidence": "对标剧 0 部命中", "examples": []})
    for name, keys in TROPE_LIBRARY.items():
        if name not in [r["name"] for r in tropes] and any(k.lower() in recent_topics for k in keys):
            diff.append({"trend": name, "kind": "趋势提及但未成对标（窗口期）",
                         "evidence": "近期趋势有提及，对标剧中暂无", "examples": []})

    data_gaps = []
    if not hits:
        data_gaps.append("hits 为空：无法拆解题材/套路")
    if not rankings:
        data_gaps.append("rankings 为空：无法做榜单洞察与市场偏好")
    if len(markets) < 2:
        data_gaps.append("markets 过少：横向对比不成立")

    obj = {
        "generated_at": C.now_iso(), "module": "S1-hit-breakdown", "schema_version": 1,
        "market_window": {k: cov["coverage"].get(k) for k in
                          ("market", "window_from", "window_to")},
        "counts": {"hits": len(hits), "rankings": len(rankings), "markets": len(markets)},
        "genre_distribution": genre_distribution,
        "tropes": tropes, "boom_elements": booms,
        "ranking_insights": ranking_insights,
        "content_preferences": prefs,
        "differentiation_trends": diff,
        "data_gaps": data_gaps,
    }

    print("  题材类型分布：")
    for r in genre_distribution[:6]:
        print("    %-14s %2d 部  %5.1f%%" % (r["genre"], r["count"], r["share_pct"]))
    print("  高频剧情套路：%s"
          % "、".join("%s(%d)" % (r["name"], r["count"]) for r in tropes[:6] if r["count"]))
    print("  高频爆点元素：%s"
          % "、".join("%s(%d)" % (r["name"], r["count"]) for r in booms[:6] if r["count"]))
    print("  榜单洞察：总 %d 条；平台 %s" % (len(rankings),
          "、".join("%s(%d)" % (x["platform"], x["count"]) for x in ranking_insights["by_platform"][:4])))
    print("  内容偏好（西语置顶）：")
    for p in prefs[:4]:
        print("    %-14s %s 套路=%s" % (p["market"], "★" if p["is_spanish"] else " ",
                                   "、".join(p["top_tropes"]) or "-"))
    print("  差异化趋势：%d 条" % len(diff))
    for d in diff[:6]:
        print("    - [%s] %s" % (d["kind"], d["trend"]))
    for g in data_gaps:
        print("  [WARN] %s" % g)

    if dry:
        print("[dry] 未落盘。")
        return 0
    p1 = C.write_json(C.pdir(ws, C.BREAKDOWN), obj)
    p2 = C.write_text(C.pdir(ws, "creation/01-breakdown/breakdown.md"), render_md(obj))
    print("\n[OK] 已写出：\n     %s\n     %s" % (p1, p2))
    print("     下一步：python topic_analysis.py scaffold → 按 prompts/s2-topic-positioning.md 填候选")
    return 0


def render_md(o: dict) -> str:
    L = ["# ② 爆款拆解（%s）" % o["generated_at"][:19], "",
         "- 样本：对标剧 %d 部 / 榜单 %d 条 / 市场 %d 个"
         % (o["counts"]["hits"], o["counts"]["rankings"], o["counts"]["markets"]), "",
         "## 一、题材类型分布", "", "| 题材 | 数量 | 占比 | 例 |", "|---|---|---|---|"]
    L += ["| %s | %d | %.1f%% | %s |" % (r["genre"], r["count"], r["share_pct"],
                                        "、".join(r["examples"])) for r in o["genre_distribution"]]
    L += ["", "## 二、剧情套路库（频次）", "", "| 套路 | 命中 | 占比 | 例 |", "|---|---|---|---|"]
    L += ["| %s | %d | %.1f%% | %s |" % (r["name"], r["count"], r["share_pct"], "、".join(r["examples"]))
          for r in o["tropes"] if r["count"]]
    L += ["", "## 三、爆点元素（频次）", "", "| 爆点 | 命中 | 例 |", "|---|---|---|"]
    L += ["| %s | %d | %s |" % (r["name"], r["count"], "、".join(r["examples"]))
          for r in o["boom_elements"] if r["count"]]
    L += ["", "## 四、榜单洞察", "",
          "- 条目总数：%d" % o["ranking_insights"]["total"], "",
          "| 平台 | 条目 |", "|---|---|"]
    L += ["| %s | %d |" % (x["platform"], x["count"]) for x in o["ranking_insights"]["by_platform"]]
    L += ["", "| 题材 | 条目 |", "|---|---|"]
    L += ["| %s | %d |" % (x["track"], x["count"]) for x in o["ranking_insights"]["by_track"]]
    L += ["", "## 五、内容偏好（按市场，西语置顶）", ""]
    for p in o["content_preferences"]:
        L.append("- **%s**%s：题材 %s；榜单赛道 %s；主套路 %s。%s"
                 % (p["market"], "（★重点）" if p["is_spanish"] else "",
                    "、".join(p["top_genres"]), "、".join(p["ranking_tracks"]) or "-",
                    "、".join(p["top_tropes"]) or "-", p["note"] or ""))
    L += ["", "## 六、差异化趋势（蓝海切口候选）", ""]
    for d in o["differentiation_trends"]:
        L.append("- **%s**（%s）：%s" % (d["trend"], d["kind"], d["evidence"]))
    if o["data_gaps"]:
        L += ["", "## 数据缺口", ""] + ["- %s" % g for g in o["data_gaps"]]
    return "\n".join(L)


def cmd_analyze(a) -> int:
    return analyze(C.resolve_ws(a.workspace), dry=a.dry)


def main() -> int:
    C.harden_stdio()
    ap = argparse.ArgumentParser(description="② 爆款拆解：题材分布 / 剧情套路 / 爆点元素 / 偏好 / 差异化趋势")
    ap.add_argument("--workspace", default=None)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("analyze", help="拆解并落盘")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=cmd_analyze)
    a = ap.parse_args()
    if not getattr(a, "fn", None):
        return analyze(C.resolve_ws(a.workspace))
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
