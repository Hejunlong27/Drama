#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""market_data.py —— 第①部分「榜单调研与对比」的数据底座

职责（**只管调研数据、不做分析结论**）
--------------------------------------
对应交付的第一部分：**梳理 TikTok 等平台爆款短剧榜单，结合北美/东南亚/拉美等海外市场数据横向对比，
重点聚焦西语市场。** 本脚本把这件事的**数据侧**固化下来：

1. `template` —— 市场数据缓存模板（含榜单 `rankings`、多市场对比 `markets`、对标剧 `hits`、趋势 `trends`）。
2. `check`    —— **覆盖度门禁**：窗口必须覆盖最近 2 个月且足够新鲜；`markets` 必须含**西语市场**；
                 榜单/对标剧/趋势/平台数达标。不过则后续环节拒绝执行（不静默用旧数据）。
3. `compare`  —— 把 `markets` 渲染成**多市场横向对比表**（北美 / 东南亚 / 拉美 / 西语 …）。
4. `queries`  —— 生成当期检索清单（含各市场榜单检索），交会话内 agent 执行后回填。

数据落地：`<workspace>/creation/market/market-data.json`（schema 见 assets/market-data.template.json）

命令
----
    python market_data.py template --write        写出模板到工作区
    python market_data.py check                   覆盖度门禁（后续环节前必跑）
    python market_data.py compare                 多市场横向对比表
    python market_data.py queries                 当期检索清单
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _common as C  # noqa: E402

# ---- 硬门槛（「最近 2 个月」口径 + 第一部分覆盖要求） ----------------------
WINDOW_MIN_DAYS = 55      # 窗口跨度下限（2 个月 ≈ 60 天，留 5 天弹性）
FRESH_MAX_DAYS = 7        # 窗口右端距今天上限：超过即视为「不新鲜」
RECENT_MAX_DAYS = 30      # 「当下趋势」要求：至少 N 条记录落在最近 30 天内
MIN_MARKETS = 3           # 多市场横向对比下限（北美/东南亚/拉美…）
MIN_RANKINGS = 8          # 榜单条目下限
MIN_HITS = 5              # 爆款对标剧下限
MIN_TRENDS = 6            # 趋势条目下限
MIN_PLATFORMS = 3         # 平台数下限
SCHEMA_VERSION = 2

SPANISH_HINTS = ("西语", "拉美", "西班牙", "latam", "spain", "hispano", "español", "spanish")

TEMPLATE = {
    "schema_version": SCHEMA_VERSION,
    "generated_at": "<YYYY-MM-DD，检索当天>",
    "market": "es-LATAM",
    "market_label": "西语（拉美 + 西班牙）—— 重点市场",
    "window": {"from": "<YYYY-MM-DD：检索日往前推 2 个月>", "to": "<YYYY-MM-DD：检索日>", "months": 2},
    "markets": [
        {
            "id": "<na>", "name": "<北美>", "lang": "<en>", "region": "<US/CA>",
            "downloads_share_pct": "<全球短剧下载占比，数字或无>",
            "mau": "<月活，如 2400万>",
            "monetization": "<IAP/IAAP/IAA 与客单价>",
            "top_genres": ["<狼人>", "<霸总>"],
            "competition": "<high|mid|low>",
            "note": "<该市场横向对比要点>",
        },
        {
            "id": "es-latam", "name": "拉美（西语）", "lang": "es", "region": "MX/CO/AR/CL/PE",
            "downloads_share_pct": "<…>", "mau": "<…>", "monetization": "<…>",
            "top_genres": ["<霸总>", "<狼人>", "<复仇>"], "competition": "<…>",
            "note": "<西语一套内容覆盖十余国，翻译成本摊薄；付费能力弱于美国>",
        },
    ],
    "platforms": [
        {"id": "<reelshort>", "name": "<ReelShort>", "region": "<US/LATAM/ES>", "note": "<西语区表现>"}
    ],
    "rankings": [
        {
            "id": "R01", "platform": "<ReelShort>", "market": "<es-LATAM>",
            "rank": 1, "title": "<剧名>", "title_zh": "<中文译名>",
            "track": "<狼人奇幻 / 霸总 / 复仇 …>", "heat": "<high|mid|low>",
            "date": "<YYYY-MM-DD：榜单日期>", "url": "<链接>",
        }
    ],
    "hits": [
        {
            "id": "H01", "title": "<西语原名>", "title_zh": "<中文译名>",
            "platform": "<ReelShort>", "lang": "es", "track": "<狼人奇幻/女频逆袭/霸总/复仇>",
            "tags": ["<werewolf>", "<revenge>"], "added": "<YYYY-MM-DD>", "heat": "<high>",
            "hook": "<一句钩子>", "evidence": "<来源>",
        }
    ],
    "trends": [
        {
            "id": "T01", "date": "<YYYY-MM-DD>", "topic": "<一句话趋势>",
            "dim": "<hotspot|trend|preference>", "evidence": "<支撑数据>", "source": "<来源名>",
        }
    ],
    "audience": {"regions": ["<Mexico>"], "age": "18-45", "gender": "<女频为主>", "note": "<画像补充>"},
    "genre_heat": [{"genre": "<霸总/CEO>", "score": 9, "evidence": "<依据>"}],
    "compliance_notes": [{"date": "<YYYY-MM-DD>", "note": "<监管/平台口径变化>"}],
    "sources": [{"title": "<标题>", "url": "<url>", "date": "<YYYY-MM-DD>"}],
}


def _collect_dated(md: dict) -> list[tuple[str, _dt.date]]:
    """收集所有带日期的记录 → [(来源标签, 日期)]，用于覆盖度与新鲜度判定。"""
    out: list[tuple[str, _dt.date]] = []
    for h in md.get("hits", []) or []:
        d = C.parse_date(h.get("added"))
        if d:
            out.append(("hit:%s" % h.get("id", h.get("title", "?")), d))
    for r in md.get("rankings", []) or []:
        d = C.parse_date(r.get("date"))
        if d:
            out.append(("ranking:%s" % r.get("id", "?"), d))
    for t in md.get("trends", []) or []:
        d = C.parse_date(t.get("date"))
        if d:
            out.append(("trend:%s" % t.get("id", "?"), d))
    for n in md.get("compliance_notes", []) or []:
        d = C.parse_date(n.get("date"))
        if d:
            out.append(("compliance", d))
    for s in md.get("sources", []) or []:
        d = C.parse_date(s.get("date"))
        if d:
            out.append(("source", d))
    return out


def is_spanish(m: dict) -> bool:
    txt = " ".join(str(m.get(k, "")) for k in ("id", "name", "lang", "region", "label")).lower()
    return (str(m.get("lang", "")).lower() in ("es", "spa", "español")
            or "es-" in str(m.get("id", "")).lower()
            or any(h in txt for h in SPANISH_HINTS))


def check(ws: pathlib.Path) -> dict:
    """覆盖度体检：{'ok':bool,'errors':[...],'warnings':[...],'coverage':{...}}"""
    errors: list[str] = []
    warnings: list[str] = []
    md = C.read_json(C.pdir(ws, C.MARKET_FILE))
    if not isinstance(md, dict) or not md:
        return {"ok": False, "errors": ["缺少市场数据：%s（先跑 `market template --write` 再回填）"
                                       % C.pdir(ws, C.MARKET_FILE)],
                "warnings": [], "coverage": {}}

    today = C.today()
    win = md.get("window") or {}
    w_from = C.parse_date(win.get("from"))
    w_to = C.parse_date(win.get("to"))
    cov: dict = {"market": md.get("market"), "window_from": str(w_from), "window_to": str(w_to),
                 "today": str(today)}

    if not w_from or not w_to:
        errors.append("window.from / window.to 缺失或格式非法（需 YYYY-MM-DD）")
    else:
        span = (w_to - w_from).days
        cov["span_days"] = span
        if span < WINDOW_MIN_DAYS:
            errors.append("数据窗口仅 %d 天，不足「最近 2 个月」（下限 %d 天）" % (span, WINDOW_MIN_DAYS))
        fresh = (today - w_to).days
        cov["freshness_days"] = fresh
        if fresh > FRESH_MAX_DAYS:
            errors.append("window.to=%s 距今 %d 天，超过新鲜度上限 %d 天 → 请重新检索并刷新缓存"
                          % (w_to, fresh, FRESH_MAX_DAYS))
        if fresh < 0:
            errors.append("window.to=%s 在未来，日期非法" % w_to)

    markets = md.get("markets") or []
    rankings = md.get("rankings") or []
    hits = md.get("hits") or []
    trends = md.get("trends") or []
    plats = md.get("platforms") or []
    cov.update({"markets": len(markets), "rankings": len(rankings), "hits": len(hits),
                "trends": len(trends), "platforms": len(plats)})
    cov["spanish_markets"] = [m.get("id") or m.get("name") for m in markets if is_spanish(m)]

    if len(markets) < MIN_MARKETS:
        errors.append("多市场对比不足：%d 个 < 下限 %d 个（应含北美/东南亚/拉美等）" % (len(markets), MIN_MARKETS))
    if not cov["spanish_markets"]:
        errors.append("markets 未包含**西语市场**（重点市场缺失）——第一部分要求「重点聚焦西语市场」")
    if len(rankings) < MIN_RANKINGS:
        errors.append("榜单条目不足：%d 条 < 下限 %d 条" % (len(rankings), MIN_RANKINGS))
    if len(hits) < MIN_HITS:
        errors.append("爆款对标剧不足：%d 条 < 下限 %d 条" % (len(hits), MIN_HITS))
    if len(trends) < MIN_TRENDS:
        errors.append("趋势条目不足：%d 条 < 下限 %d 条" % (len(trends), MIN_TRENDS))
    if len(plats) < MIN_PLATFORMS:
        errors.append("平台覆盖不足：%d 个 < 下限 %d 个" % (len(plats), MIN_PLATFORMS))

    dated = _collect_dated(md)
    cov["dated_records"] = len(dated)
    if not dated:
        errors.append("没有任何带日期的记录，无法判定覆盖度")
    else:
        recent = [d for _, d in dated if 0 <= (today - d).days <= RECENT_MAX_DAYS]
        cov["recent_30d"] = len(recent)
        if len(recent) < 3:
            errors.append("最近 30 天内仅 %d 条记录（下限 3）→ 无法支撑「贴合当下趋势」" % len(recent))
        if w_from and w_to:
            in_win = [d for _, d in dated if w_from <= d <= w_to]
            cov["in_window"] = len(in_win)
            if len(in_win) < len(dated) * 0.6:
                warnings.append("仅 %d/%d 条记录落在声明的窗口内，窗口声明与数据不符"
                                % (len(in_win), len(dated)))

    if not md.get("sources"):
        warnings.append("sources 为空：建议保留检索来源，便于复核（不影响放行）")
    if not md.get("genre_heat"):
        warnings.append("genre_heat 为空：题材热度将缺少直接依据")

    return {"ok": not errors, "errors": errors, "warnings": warnings, "coverage": cov}


def compare(ws: pathlib.Path) -> list[dict]:
    """多市场横向对比（第一部分的核心交付）——纯确定性取数与排序。"""
    md = C.read_json(C.pdir(ws, C.MARKET_FILE)) or {}
    rows = []
    for m in md.get("markets", []) or []:
        rows.append({
            "market": m.get("name") or m.get("id"),
            "lang": m.get("lang"), "region": m.get("region"),
            "downloads_share_pct": m.get("downloads_share_pct"), "mau": m.get("mau"),
            "monetization": m.get("monetization"),
            "top_genres": m.get("top_genres") or [],
            "competition": m.get("competition"), "note": m.get("note"),
            "is_spanish": is_spanish(m),
        })
    # 西语市场置顶，其余按 competition 难度升序（low→high）便于「先易后难」判断
    rank = {"low": 0, "mid": 1, "high": 2, "": 3}
    rows.sort(key=lambda r: (0 if r["is_spanish"] else 1, rank.get(str(r.get("competition") or ""), 3)))
    return rows


def build_queries(months: int = 2) -> list[dict]:
    """当期检索清单（agent 执行 WebSearch 后回填 market-data.json）。"""
    today = C.today()
    ym = "%d年%d月" % (today.year, today.month)
    win_from = today - _dt.timedelta(days=months * 30)
    return [
        # 榜单（各市场）
        {"id": "Q1", "dim": "ranking", "q": "TikTok 短剧 热播榜 %s" % ym, "why": "TikTok 榜单（主平台）"},
        {"id": "Q2", "dim": "ranking", "q": "ReelShort DramaBox 西语 短剧 榜单 %s" % ym,
         "why": "西语区头部平台榜单"},
        {"id": "Q3", "dim": "ranking", "q": "北美 短剧 榜单 爆款 %s" % ym, "why": "北美市场榜单（横向对比）"},
        {"id": "Q4", "dim": "ranking", "q": "东南亚 短剧 榜单 爆款 %s" % ym, "why": "东南亚市场榜单（横向对比）"},
        {"id": "Q5", "dim": "ranking", "q": "拉美 西语 短剧 榜单 %s" % ym, "why": "拉美西语市场榜单（重点）"},
        # 市场体量
        {"id": "Q6", "dim": "market", "q": "Sensor Tower 短剧 下载 占比 拉美 东南亚 北美 %d" % today.year,
         "why": "多市场体量占比（对比依据）"},
        {"id": "Q7", "dim": "market", "q": "短剧 西语 市场 新增量 变现 %s" % ym,
         "why": "西语市场变现与增速（窗口 %s ~ %s）" % (win_from, today)},
        # 偏好与题材
        {"id": "Q8", "dim": "preference", "q": "webtoon 西语 热门 题材 %s 狼人 霸总 复仇" % ym,
         "why": "受众题材偏好（漫画侧迁移）"},
        {"id": "Q9", "dim": "preference", "q": "拉美 观众 短剧 偏好 家庭 阶层 都市浪漫 %s" % ym,
         "why": "本土文化代入点"},
        # 合规
        {"id": "Q10", "dim": "compliance", "q": "微短剧 监管 新规 出海 合规 %d" % today.year,
         "why": "合规红线（后续一票否决依据）"},
    ]


# ---------------------------------------------------------------------------
def cmd_template(a) -> int:
    ws = C.resolve_ws(a.workspace)
    import json
    text = json.dumps(TEMPLATE, ensure_ascii=False, indent=2)
    print(text)
    if a.write:
        p = C.write_text(C.pdir(ws, C.MARKET_FILE), text)
        print("\n[OK] 模板已写出：%s" % p)
        print("     下一步：按 `market queries` 检索，把结果回填该文件后跑 `market check`。")
    return 0


def cmd_check(a) -> int:
    ws = C.resolve_ws(a.workspace)
    res = check(ws)
    print(C.hr())
    print("① 榜单调研与对比 · 数据覆盖度门禁")
    print(C.hr())
    cov = res.get("coverage") or {}
    labels = [("market", "重点市场"), ("window_from", "窗口起"), ("window_to", "窗口止"),
              ("span_days", "跨度(天)"), ("freshness_days", "距今天数"),
              ("markets", "市场数"), ("spanish_markets", "西语市场"),
              ("rankings", "榜单条目"), ("hits", "对标剧"), ("trends", "趋势条目"),
              ("platforms", "平台数"), ("recent_30d", "近30天记录")]
    for k, label in labels:
        if k in cov:
            print("  %-12s: %s" % (label, cov[k]))
    for w in res["warnings"]:
        print("  [WARN] %s" % w)
    for e in res["errors"]:
        print("  [FAIL] %s" % e)
    print("-" * 74)
    if res["ok"]:
        print("[OK] 覆盖度通过：数据够新够全，可进入「② 爆款拆解」。")
        return 0
    print("[FAIL] 覆盖度不达标：请先刷新数据（`market queries` → 检索 → 回填 → `market check`）。")
    return 2


def cmd_compare(a) -> int:
    ws = C.resolve_ws(a.workspace)
    rows = compare(ws)
    print(C.hr())
    print("① 榜单调研与对比 · 多市场横向对比（西语置顶）")
    print(C.hr())
    if not rows:
        print("[FAIL] markets 为空：%s" % C.pdir(ws, C.MARKET_FILE))
        return 2
    for r in rows:
        flag = "★西语重点" if r["is_spanish"] else ""
        print("  %-16s lang=%-3s 下载占比=%-8s MAU=%-8s 竞争=%-5s %s"
              % (r["market"], r["lang"], r["downloads_share_pct"], r["mau"],
                 r["competition"], flag))
        print("      题材：%s" % "、".join(r["top_genres"]))
        print("      变现：%s" % r["monetization"])
        if r["note"]:
            print("      要点：%s" % r["note"])
    md = C.read_json(C.pdir(ws, C.MARKET_FILE)) or {}
    rk = md.get("rankings") or []
    if rk:
        print("-" * 74)
        print("榜单条目（%d 条，前 12）：" % len(rk))
        for x in rk[:12]:
            print("  %-12s %-10s #%-3s %-28s %s"
                  % (x.get("platform"), x.get("market"), x.get("rank"),
                     str(x.get("title_zh") or x.get("title"))[:28], x.get("track")))
    return 0


def cmd_queries(a) -> int:
    qs = build_queries(a.months)
    print(C.hr())
    print("① 榜单调研 · 当期检索清单（WebSearch 执行，结果回填 market-data.json）")
    print(C.hr())
    for q in qs:
        print("  [%s] (%s) %s" % (q["id"], q["dim"], q["q"]))
        print("       目的：%s" % q["why"])
    print("-" * 74)
    print("回填后务必跑：python market_data.py check（覆盖度不过，后续环节不予放行）")
    return 0


def main() -> int:
    C.harden_stdio()
    ap = argparse.ArgumentParser(description="① 榜单调研与对比：模板 / 覆盖度门禁 / 多市场对比 / 检索清单")
    ap.add_argument("--workspace", default=None)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("template", help="写出市场数据模板")
    p.add_argument("--write", action="store_true")
    p.set_defaults(fn=cmd_template)
    p = sub.add_parser("check", help="覆盖度门禁（近 2 个月 + 多市场 + 西语市场）")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("compare", help="多市场横向对比表")
    p.set_defaults(fn=cmd_compare)
    p = sub.add_parser("queries", help="打印当期检索清单")
    p.add_argument("--months", type=int, default=2)
    p.set_defaults(fn=cmd_queries)
    a = ap.parse_args()
    if not getattr(a, "fn", None):
        return cmd_check(a)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
