#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""test_creation_pipeline.py —— 西语漫剧创作链路离线测试（零网络、零计费、零第三方依赖）

覆盖四部分的确定性核心：
  ① 榜单调研与对比：覆盖度门禁（新鲜→过 / 过期→拦 / 缺西语市场→拦）、多市场对比、检索清单
  ② 爆款拆解：题材分布 / 剧情套路 / 爆点元素 / 榜单洞察 / 内容偏好 / 差异化趋势
  ③ 选题定位：七维评分 / 合规一票否决 / 三维交叉 / 硬约束 / 赛道去重 / 定位三要素 / 定位卡
  ④ 剧本改写：源剧本登记 / N 集规划（每集冲突+反转+钩子）/ 爆点节拍 / FFS 骨架 / 结构自检

用法：python test_creation_pipeline.py     （全绿退出码 0）
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import io
import json
import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # stages/creation/scripts
sys.path.insert(0, str(HERE))                 # tests

import _common as C  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*a, **kw)
    return rc, buf.getvalue()


def _mk_market(today: _dt.date, to_offset_days: int = 0, with_spanish: bool = True,
               n_rankings: int = 10) -> dict:
    w_to = today - _dt.timedelta(days=to_offset_days)
    w_from = w_to - _dt.timedelta(days=60)
    markets = [
        {"id": "na", "name": "北美", "lang": "en", "region": "US/CA",
         "downloads_share_pct": "消费额占全球约37%", "mau": "-", "monetization": "IAP 为主",
         "top_genres": ["狼人", "霸总", "复仇"], "competition": "high", "note": "付费能力最强"},
        {"id": "sea", "name": "东南亚", "lang": "multi", "region": "ID/TH/PH",
         "downloads_share_pct": "32%", "mau": "-", "monetization": "IAAP/IAA",
         "top_genres": ["逆袭", "豪门"], "competition": "high", "note": "下载量最大"},
    ]
    if with_spanish:
        markets.append({"id": "es-latam", "name": "拉美（西语）", "lang": "es", "region": "MX/CO/AR",
                        "downloads_share_pct": "23%", "mau": "4400万", "monetization": "广告+订阅",
                        "top_genres": ["霸总", "狼人", "复仇"], "competition": "mid",
                        "note": "一套西语覆盖十余国"})
    hits = [{"id": "H%02d" % i, "title": "西语爆款 %d" % i, "title_zh": "西语爆款 %d" % i,
             "platform": ["ReelShort", "ShortTV", "DramaBox"][i % 3], "lang": "es",
             "track": ["狼人奇幻", "霸总", "复仇"][i % 3],
             "tags": [["werewolf", "revenge", "betrayal"], ["ceo", "contract"], ["revenge", "identity"]][i % 3],
             "added": str(w_to - _dt.timedelta(days=4 * i)), "heat": "high",
             "hook": "血月夜觉醒，身份揭穿", "evidence": "microdramalist"} for i in range(1, 7)]
    rankings = [{"id": "R%02d" % i, "platform": ["ReelShort", "ShortTV", "TikTok"][i % 3],
                 "market": ["es-latam", "na", "sea"][i % 3], "rank": i,
                 "title": "榜单剧 %d" % i, "title_zh": "榜单剧 %d" % i,
                 "track": ["狼人奇幻", "霸总", "复仇", "逆袭"][i % 4], "heat": "high",
                 "date": str(w_to - _dt.timedelta(days=i)), "url": ""} for i in range(1, n_rankings + 1)]
    trends = [{"id": "T%02d" % i, "date": str(w_to - _dt.timedelta(days=2 * i)),
               "topic": "趋势 %d" % i, "dim": ["trend", "preference", "hotspot"][i % 3],
               "evidence": "证据 %d" % i, "source": "来源"} for i in range(1, 8)]
    return {
        "schema_version": 2, "generated_at": str(today), "market": "es-LATAM",
        "window": {"from": str(w_from), "to": str(w_to), "months": 2},
        "markets": markets,
        "platforms": [{"id": "reelshort", "name": "ReelShort", "region": "LATAM", "note": ""},
                      {"id": "shorttv", "name": "ShortTV", "region": "LATAM", "note": ""},
                      {"id": "tiktok", "name": "TikTok", "region": "global", "note": ""}],
        "rankings": rankings, "hits": hits, "trends": trends,
        "audience": {"regions": ["Mexico"], "age": "18-45", "gender": "女频为主", "note": ""},
        "genre_heat": [{"genre": "狼人", "score": 9, "evidence": "对标剧密集"}],
        "compliance_notes": [{"date": str(w_to - _dt.timedelta(days=10)), "note": "审核口径"}],
        "sources": [{"title": "来源", "url": "http://example.invalid", "date": str(w_to)}],
    }


def _cand(cid, track, base, compliance=9, basis_all=True, ev=("H01", "T01", "T03")):
    dims = {"market_heat": base, "trend_freshness": base, "audience_fit": base,
            "platform_fit": base, "hook_strength": base, "differentiation": base,
            "compliance": compliance}
    basis = {k: "依据：对标剧/榜单 %s" % k for k in dims}
    if not basis_all:
        basis.pop("hook_strength")
    return {"id": cid, "name_zh": "选题 %s" % cid, "name_es": "Tema %s" % cid,
            "logline": "主角 %s 的困局与钩子" % cid, "conflict": "主角 vs 对手 vs 命运",
            "track": track, "audience": "拉美女性 18-40", "platform": "ReelShort",
            "scores": dims, "score_basis": basis,
            "hook_formula": ["反转", "觉醒"], "differentiation_anchor": "与头部剧的差异点 %s" % cid,
            "selling_points": ["卖点A-%s" % cid, "卖点B-%s" % cid],
            "freshness_evidence": list(ev),
            "production_path": {"cost_tier": "低", "cycle": "2周", "ai_fit": "高", "series": True},
            "risks": []}


def main() -> int:
    today = C.today()
    ws = pathlib.Path(tempfile.mkdtemp(prefix="drama-creation-test-"))
    try:
        import market_data as MD
        import hit_breakdown as HB
        import topic_analysis as TA
        import hit_selection as HS
        import script_rewrite as SR

        C.write_json(ws / C.BRIEF, {"slug": "t", "title": "测试剧", "platform": "ReelShort",
                                    "market": "es-LATAM", "audience": "拉美女性 18-40",
                                    "ep_total": 40, "ep_seconds": 70, "form": "AI漫剧"})   # ★ 2026-10-16 默认口径：40 集 × 70 秒

        # ================= ① 榜单调研与对比 =================
        C.write_json(ws / C.MARKET_FILE, _mk_market(today))
        res = MD.check(ws)
        check("① 新鲜数据覆盖度通过", res["ok"] is True, str(res["errors"]))
        check("① 覆盖度含西语市场", "es-latam" in (res["coverage"].get("spanish_markets") or []),
              json.dumps(res["coverage"].get("spanish_markets"), ensure_ascii=False))

        C.write_json(ws / C.MARKET_FILE, _mk_market(today, to_offset_days=40))
        res2 = MD.check(ws)
        check("① 过期数据被拦", res2["ok"] is False and any("新鲜度" in e for e in res2["errors"]))

        C.write_json(ws / C.MARKET_FILE, _mk_market(today, with_spanish=False))
        res3 = MD.check(ws)
        check("① 缺西语市场被拦", res3["ok"] is False and any("西语市场" in e for e in res3["errors"]))

        C.write_json(ws / C.MARKET_FILE, _mk_market(today, n_rankings=3))
        res4 = MD.check(ws)
        check("① 榜单不足被拦", res4["ok"] is False and any("榜单条目不足" in e for e in res4["errors"]))

        C.write_json(ws / C.MARKET_FILE, _mk_market(today))     # 复原
        rows = MD.compare(ws)
        check("① 多市场对比返回 3 个市场", len(rows) == 3, str(len(rows)))
        check("① 西语市场置顶", rows and rows[0]["is_spanish"] is True)
        check("① 检索清单生成", len(MD.build_queries(2)) >= 8)

        # ================= ② 爆款拆解 =================
        rc, _ = _quiet(HB.analyze, ws, False)
        bd = C.read_json(ws / C.BREAKDOWN) or {}
        check("② 落盘拆解结论", bool(bd))
        check("② 题材类型分布非空", len(bd.get("genre_distribution") or []) >= 2)
        tropes_hit = [t for t in (bd.get("tropes") or []) if t["count"] > 0]
        booms_hit = [b for b in (bd.get("boom_elements") or []) if b["count"] > 0]
        check("② 剧情套路命中（狼人/复仇等）", len(tropes_hit) >= 2,
              json.dumps([t["name"] for t in tropes_hit], ensure_ascii=False))
        check("② 爆点元素命中（觉醒/身份揭穿等）", len(booms_hit) >= 1,
              json.dumps([b["name"] for b in booms_hit], ensure_ascii=False))
        check("② 榜单洞察聚合", (bd.get("ranking_insights") or {}).get("total", 0) >= 8)
        check("② 内容偏好含西语且置顶",
              bd.get("content_preferences") and bd["content_preferences"][0]["is_spanish"] is True)
        check("② 差异化趋势非空", isinstance(bd.get("differentiation_trends"), list)
              and len(bd["differentiation_trends"]) >= 1,
              str(len(bd.get("differentiation_trends") or [])))

        # ================= ③ 选题定位 =================
        raw = [
            _cand("C01", "狼人奇幻", 9), _cand("C02", "狼人奇幻", 8), _cand("C03", "狼人奇幻", 7),
            _cand("C04", "狼人奇幻", 9, compliance=2),      # 一票否决
            _cand("C05", "霸总", 8), _cand("C06", "霸总", 7),
            _cand("C07", "复仇", 7),
            _cand("C08", "逆袭", 6, basis_all=False),        # 依据缺失 → 不合格
            _cand("C09", "逆袭", 6),
        ]
        C.write_json(ws / C.RAW_CANDIDATES, raw)
        rc, _ = _quiet(TA.run, ws, False)
        obj = C.read_json(ws / C.CANDIDATES) or {}
        cands = {c["id"]: c for c in obj.get("candidates", [])}
        check("③A 落盘候选池", bool(obj) and len(cands) == 9)
        check("③A 三维交叉命中", bool(cands["C01"]["cross"]["triple"]),
              json.dumps(cands["C01"]["cross"], ensure_ascii=False))
        check("③A 合规一票否决 → D", cands["C04"]["veto"] is True and cands["C04"]["grade"] == "D")
        check("③A 评分依据缺失 → 不合格", cands["C08"]["status"] == "error")
        check("③A 赛道超占比降级", any(c["status"] == "demoted" for c in obj["candidates"]))
        check("③A 七维权重合计为 1", abs(sum(w for _, _, w in TA.DIMS) - 1.0) < 1e-9)
        pos = cands["C01"].get("positioning") or {}
        check("③A 定位三要素齐备",
              bool(pos.get("audience_profile")) and bool(pos.get("genre_direction"))
              and bool(pos.get("differentiation_selling_points")),
              json.dumps(pos, ensure_ascii=False)[:140])
        check("③A 接入②拆解校准", obj.get("breakdown_used") is True and bool(pos.get("market_hot_tropes")))

        rc, _ = _quiet(HS.select, ws, 3, None, False)
        sel = C.read_json(ws / C.POSITIONING) or {}
        check("③B 落盘定位卡", bool(sel) and len(sel.get("selected", [])) == 3)
        tracks = [x["genre_direction"] for x in sel.get("selected", [])]
        check("③B 多样性：Top-N 赛道互不相同", len(set(tracks)) == len(tracks), str(tracks))
        card = sel["selected"][0]
        check("③B 定位卡含受众/题材/卖点/红线",
              bool(card.get("audience_profile")) and bool(card.get("genre_direction"))
              and bool(card.get("differentiation_selling_points")) and len(card.get("differentiation_redlines", [])) >= 4)
        check("③B 含②校准结论", "stance" in (card.get("breakdown_alignment") or {}))
        check("③B 剔除被否决与不合格",
              any("否决" in r["reason"] for r in sel["rejected"]))

        # ================= ④ 剧本改写 =================
        C.write_text(ws / C.SOURCE_SCRIPT, "# 源剧本（很颠）\n第一幕\nINT. 宫殿 - 夜\nEXT. 悬崖 - 雨夜\n")
        rc, out = _quiet(SR.plan, ws, 1, None, False)
        bp = C.read_json(ws / C.BLUEPRINT) or {}
        check("④ 落盘集数规划", bool(bp))
        # ★ 2026-10-16：默认集数 40（30→40）；节拍 中型每 4 / 大型每 10（与 60 集口径密度一致）
        check("④ 规划 40 集（默认口径）", bp.get("total_ep") == 40 and len(bp.get("episodes", [])) == 40,
              "n=%s" % bp.get("total_ep"))
        ep1 = (bp.get("episodes") or [{}])[0]
        check("④ 每集含 冲突/反转/钩子/钩子类型",
              all(ep1.get(k) for k in ("core_conflict", "reversal", "hook", "hook_type")),
              json.dumps(ep1, ensure_ascii=False)[:160])
        all_have = all(all(e.get(k) for k in ("core_conflict", "reversal", "hook"))
                       for e in bp.get("episodes", []))
        check("④ 全集均含三件套", all_have)
        # ★ 留存率结构（2026-10-16）：四件套 + EP1 黄金3秒 + 三段留存
        eps = bp.get("episodes", [])
        check("④ 每集含留存四件套",
              all(all(str(e.get(k) or "").strip()
                      for k in ("cold_open", "core_conflict", "reversal", "hook")) for e in eps))
        check("④ EP1 为黄金 3 秒", "黄金 3 秒" in str(ep1.get("cold_open") or ""),
              str(ep1.get("cold_open"))[:40])
        check("④ 留存三段（拉新攻坚/留存活口/转化与收束）",
              [e.get("retention_role") for e in eps[:3]] == ["拉新攻坚"] * 3
              and eps[3].get("retention_role") == "留存活口"
              and eps[10].get("retention_role") == "转化与收束")
        check("④ 留存结构声明齐备", isinstance(bp.get("retention_plan"), dict)
              and bp["retention_plan"].get("ep1_golden_3s"))
        check("④ 源剧本已登记", (bp.get("source_script") or {}).get("present") is True)
        _bc = bp["boom_cadence"]
        _mid = 40 // 4 - len([i for i in range(1, 41) if i % 4 == 0 and i % 10 == 0])
        check("④ 爆点节拍（40 集：中型 %d / 大型 %d）" % (_mid, 40 // 10),
              _bc["mid_count"] == _mid and _bc["big_count"] == 40 // 10,
              json.dumps(_bc, ensure_ascii=False))
        check("④ 钩子链前 3 集", [h["type"] for h in bp["hook_chain"]] == ["身份悬念", "危机逼近", "秘密揭露"])
        check("④ 幕结构 4 幕", len(bp.get("act_structure", [])) == 4)
        check("④ STYLE LOCK 含底线", "no watermark" in (bp.get("style_lock") or ""))

        rc, _ = _quiet(SR.build, ws, 1, 8, True, False)
        sk = ws / "creation/03-script/skeleton_ep_01.md"
        emitted = ws / "script/ep_01.md"
        sk_text = sk.read_text(encoding="utf-8") if sk.is_file() else ""
        check("④ FFS 骨架 8 个镜头块", sk_text.count("[SC-") == 8)
        check("④ 骨架 HEADER 六字段齐备",
              all(("# %s:" % f) in sk_text for f in ["TITLE", "EP", "PLATFORM", "STYLE LOCK", "CHAR", "SCENE"]))
        check("④ --emit 就位到 script/", emitted.is_file())

        # lint：好 / 坏剧本
        def _ffs(blocks: int, drop_field: str | None = None) -> str:
            head = ("# TITLE: 测试剧\n# EP: 01/40\n# PLATFORM: ReelShort\n"
                    "# STYLE LOCK: %s\n# CHAR: AYLA: x\n# SCENE: CLIFF: y\n" % SR.STYLE_LOCKS["狼人"])
            out = [head]
            for i in range(1, blocks + 1):
                f = {"SHOT": "中景, 固定", "LOCATION": "CLIFF", "TIME": "夜", "CHAR": "AYLA",
                     "ACTION": "AYLA 推开门", "DIALOG": '"Vete"', "VO": "无", "SFX": "无", "DUR": "8"}
                if drop_field:
                    f.pop(drop_field, None)
                out.append("---\n[SC-%02d]\n" % i + "\n".join("%s: %s" % (k, v) for k, v in f.items()))
            return "\n\n".join(out)

        C.write_text(ws / "script/ep_01.md", _ffs(10))
        rc, out = _quiet(SR.lint, ws, 1)
        check("④ lint 合格剧本通过", rc == 0, out[-160:])
        C.write_text(ws / "script/ep_01.md", _ffs(10, drop_field="ACTION"))
        rc, out = _quiet(SR.lint, ws, 1)
        check("④ lint 缺字段被拦", rc == 1)
        C.write_text(ws / "script/ep_01.md", _ffs(3))
        rc, out = _quiet(SR.lint, ws, 1)
        check("④ lint 镜头数越界被拦", rc == 1)
        C.write_text(ws / "script/ep_01.md", _ffs(10).replace("DUR: 8", "MOOD: 紧张\nDUR: 8", 1))
        rc, out = _quiet(SR.lint, ws, 1)
        check("④ lint 禁字段被拦", rc == 1)

        # ================= 契约完整性 =================
        for rel in (C.MARKET_FILE, C.BREAKDOWN, C.CANDIDATES, C.POSITIONING, C.BLUEPRINT):
            check("产物存在：%s" % rel, (ws / rel).is_file())
    finally:
        shutil.rmtree(ws, ignore_errors=True)

    print("=" * 62)
    print("creation 创作链路离线测试（四部分）")
    print("=" * 62)
    n_fail = 0
    for name, ok, detail in RESULTS:
        print("  %s %s%s" % ("[OK]  " if ok else "[FAIL]", name, "" if ok else "  <- %s" % detail))
        n_fail += 0 if ok else 1
    print("-" * 62)
    print("共 %d 例，通过 %d，失败 %d" % (len(RESULTS), len(RESULTS) - n_fail, n_fail))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
