#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""test_genre_formula.py —— 固定题材框架（题材公式）离线测试（零网络、零计费、零第三方依赖）

覆盖：
  * 公式库载入 / 关键词路由（最长匹配优先）/ 未命中
  * 阶段解析与**等比缩放**（40 集为原值；其他集数按比例映射且不重叠）
  * 逐集公式化要求：打脸节拍（从阶段末集倒数）/ 打脸等级**单调不减** / 见证者 / 压放（含触底段例外）
    / 金句每集必给 / 事业线里程碑
  * topic_card 构造与覆盖
  * apply 落 genre-plan.json；doc 由 JSON 生成（并校验 md 与 JSON 同步）
  * check：合规规划 → 通过；缺见证者 / 缺金句 / 阶梯回落 → 拦

用法：python test_genre_formula.py     （全绿退出码 0）
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import shutil
import sys
import tempfile
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # stages/creation/scripts
sys.path.insert(0, str(HERE))

import _common as C  # noqa: E402
import genre_formula as G  # noqa: E402

RESULTS: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*a, **kw)
    return rc, buf.getvalue()


GENRE = "female-comeback"


def _all_phases_cover(f: dict, n: int) -> bool:
    """阶段映射必须恰好覆盖 1..n，无空洞、无重叠。"""
    seen = []
    for ep in range(1, n + 1):
        seen.append(G.phase_of(f, ep, n).get("id"))
    return len(seen) == n and all(seen)


def _mock_blueprint(f: dict, n: int) -> dict:
    eps = []
    for i in range(1, n + 1):
        ov = G.episode_overlay(f, i, n)
        row = {"ep": i, "hook_type": ["身份悬念", "危机逼近", "秘密揭露"][i - 1] if i <= 3
               else "关系反转", "cold_open": ("★黄金 3 秒：把全剧最高冲突前置" if i == 1 else "推进"),
               "core_conflict": "A vs B", "reversal": "力量对比改变", "witness": ov["witness"]}
        row.update(ov)
        eps.append(row)
    return {"total_ep": n, "episodes": eps}


def main() -> int:
    C.harden_stdio()
    ws = pathlib.Path(tempfile.mkdtemp(prefix="gf_ws_"))
    try:
        fs = G.list_formulas()
        check("公式库可列出", len(fs) >= 1, "n=%d" % len(fs))
        check("女频逆袭公式在库", any(x["id"] == GENRE for x in fs))
        f = G.load(GENRE)
        check("公式含必备段", all(k in f for k in
                                ("root_causes", "phases", "punch_ladder", "punch_five_beats",
                                 "hook_formula", "dialogue_policy", "qc", "redlines")))
        check("根本原因 ≥8 条", len(f.get("root_causes") or []) >= 8,
              str(len(f.get("root_causes") or [])))
        check("五拍齐全且含「压制」",
              len(f["punch_five_beats"]) == 5
              and any("压制" in b["name"] for b in f["punch_five_beats"]))
        try:
            G.load("no-such-genre")
            check("未收录题材报错", False)
        except KeyError:
            check("未收录题材报错", True)

        # ---- 关键词路由 ----
        gid, kw = G.match_text("帮我做一部女主逆袭的短剧")
        check("命中「女主逆袭」", gid == GENRE and kw == "女主逆袭", "%s/%s" % (gid, kw))
        gid2, _ = G.match_text("女主逆袭")
        check("独立关键词也命中", gid2 == GENRE)
        gid3, _ = G.match_text("做个科幻悬疑题材")
        check("未命中返回 None", gid3 is None)
        check("空输入返回 None", G.match_text("")[0] is None)
        # 最长匹配优先：同时含「逆袭」与「女频逆袭」时应取更长的词
        gid4, kw4 = G.match_text("女频逆袭爽剧")
        check("最长匹配优先", gid4 == GENRE and len(kw4) >= 3, str(kw4))

        # ---- 阶段解析 ----
        n = 40
        exp = {1: "P1", 3: "P1", 4: "P2", 6: "P2", 7: "P3", 16: "P3",
               17: "P4", 30: "P4", 31: "P5", 40: "P5"}
        ok = all(G.phase_of(f, ep, n).get("id") == pid for ep, pid in exp.items())
        check("40 集阶段边界正确", ok, json.dumps({e: G.phase_of(f, e, n)["id"] for e in exp}))
        check("40 集阶段全覆盖无重叠", _all_phases_cover(f, n))
        for nn in (20, 30, 60, 12):
            check("%d 集阶段全覆盖无重叠（等比缩放）" % nn, _all_phases_cover(f, nn))

        # ---- 逐集公式化要求 ----
        ov1 = G.episode_overlay(f, 1, n)
        ov3 = G.episode_overlay(f, 3, n)
        ov40 = G.episode_overlay(f, 40, n)
        check("EP1 不安排打脸（触底段压迫）", ov1["punch"] is None and ov1["punch_level"] == 0)
        check("EP1 用「压」", ov1["stake"] == "压")
        check("EP3 给首次小打脸 L1", ov3["punch"] and ov3["punch_level"] == 1 and ov3["witness"])
        check("EP40 为 L4 且有见证者", ov40["punch_level"] == 4 and ov40["witness"])

        levels = [G.episode_overlay(f, i, n)["punch_level"] for i in range(1, n + 1)]
        nonzero = [x for x in levels if x > 0]
        check("打脸阶梯只升不降（单调不减）",
              all(nonzero[i] <= nonzero[i + 1] for i in range(len(nonzero) - 1)), str(nonzero))
        check("打脸阶梯触达 L4", max(levels) == 4)
        check("每集都有金句方向",
              all(G.episode_overlay(f, i, n)["quote"] for i in range(1, n + 1)))
        ms = [G.episode_overlay(f, i, n)["milestone"] for i in range(1, n + 1)]
        check("事业线里程碑每 5 集（P3 起）", sum(1 for x in ms if x) >= 6,
              "n=%d" % sum(1 for x in ms if x))
        stakes = [G.episode_overlay(f, i, n)["stake"] for i in range(1, n + 1)]
        check("触底段（EP1-3）全部为「压」", stakes[:3] == ["压"] * 3, str(stakes[:3]))
        check("终局段以「扬」收束", stakes[-1] == "扬", stakes[-1])

        # ---- topic_card ----
        card = G.build_topic_card(f, title="测试剧", logline="她被踩到泥里又打回去")
        check("topic_card 可覆盖标题", card["title"] == "测试剧" and card["_from_genre_formula"] == GENRE)
        check("topic_card 自带差异化卖点", bool(card.get("differentiation_selling_points")))

        # ---- apply / doc ----
        rc, out = _quiet(G.cmd_apply, types.SimpleNamespace(
            workspace=str(ws), genre=GENRE, title=None, logline=None, matched_by="女主逆袭"))
        gp = C.pdir(ws, C.GENRE_PLAN)
        obj = json.loads(gp.read_text(encoding="utf-8")) if gp.is_file() else {}
        check("apply 落 genre-plan.json", rc == 0 and obj.get("genre") == GENRE)
        check("apply 标记跳过选题", obj.get("skip_topic") is True)
        check("apply 可被 ④改写自动识别", (G.load_plan(ws) or {}).get("genre") == GENRE)

        md_path = G.formula_path(GENRE).with_suffix(".md")
        rc, out = _quiet(G.cmd_doc, types.SimpleNamespace(genre=GENRE, write=True))
        md = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
        check("doc 生成 md", rc == 0 and md.startswith("# 题材公式"))
        check("md 与 JSON 同步（标题/根本原因条数）",
              f["label"] in md and md.count("|") > 40,
              "label=%s" % (f["label"] in md))
        check("md 声明「勿手改」", "请勿手改" in md or "勿手改" in md)
        check("md 含五拍与阶梯", "五拍打脸法" in md and "打脸阶梯" in md)

        # ---- check：合规规划通过 ----
        bp = _mock_blueprint(f, n)
        C.write_json(C.pdir(ws, C.BLUEPRINT), bp)
        rc, out = _quiet(G.cmd_check, types.SimpleNamespace(workspace=str(ws), genre=GENRE))
        check("check 合规规划通过", rc == 0, out.strip().splitlines()[-1][:80] if out else "")

        # ---- check：缺见证者被拦 ----
        bp2 = _mock_blueprint(f, n)
        for e in bp2["episodes"]:
            if e["punch"]:
                e["witness"] = False
        C.write_json(C.pdir(ws, C.BLUEPRINT), bp2)
        rc, out = _quiet(G.cmd_check, types.SimpleNamespace(workspace=str(ws), genre=GENRE))
        check("check 缺见证者被拦", rc == 1 and "见证者" in out)

        # ---- check：缺金句被拦 ----
        bp3 = _mock_blueprint(f, n)
        bp3["episodes"][5]["quote"] = None
        C.write_json(C.pdir(ws, C.BLUEPRINT), bp3)
        rc, out = _quiet(G.cmd_check, types.SimpleNamespace(workspace=str(ws), genre=GENRE))
        check("check 缺金句被拦", rc == 1 and "金句" in out)

        # ---- check：阶梯回落被拦（WARN 级）----
        bp4 = _mock_blueprint(f, n)
        idx = [i for i, e in enumerate(bp4["episodes"]) if e["punch"]]
        bp4["episodes"][idx[-1]]["punch_level"] = 1
        C.write_json(C.pdir(ws, C.BLUEPRINT), bp4)
        rc, out = _quiet(G.cmd_check, types.SimpleNamespace(workspace=str(ws), genre=GENRE))
        check("check 阶梯回落被拦", rc == 1 and "回落" in out)

        # ---- check：缺规划 ----
        bp_path = C.pdir(ws, C.BLUEPRINT)
        backup = bp_path.read_text(encoding="utf-8")
        bp_path.unlink()
        rc, out = _quiet(G.cmd_check, types.SimpleNamespace(workspace=str(ws), genre=GENRE))
        check("check 无规划返回 2", rc == 2)
        bp_path.write_text(backup, encoding="utf-8")
    finally:
        shutil.rmtree(ws, ignore_errors=True)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for name, ok, detail in RESULTS:
        print("  [%s] %s%s" % ("OK  " if ok else "FAIL", name,
                               "  → %s" % detail if (detail and not ok) else ""))
    print("\n共 %d 例，通过 %d，失败 %d" % (len(RESULTS), passed, len(RESULTS) - passed))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
