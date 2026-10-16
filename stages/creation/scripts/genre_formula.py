#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""genre_formula.py —— 固定题材框架（题材公式）：关键词路由 + 公式套用 + 结构校验

为什么存在
----------
用户实测两部试验剧「剧本过于简单、缺乏爽点引爆感」—— 病根不在演员与出片，而在**剧本结构**：
压迫不透、打脸没阶梯、没有见证者、台词无攻击性、前 3 集先铺垫。

因此把题材套路从「写作建议」升级为**可复用的公式资产**：

    references/genre-formulas/<id>.json   机器真源（结构化：阶段/打脸阶梯/五拍/QC 阈值）
    references/genre-formulas/<id>.md     由本脚本 `doc` 生成的人读/LLM 读版（勿手改）
    <工作区>/03-剧本/genre-plan.json      `apply` 落地的套用单（含 skip_topic 标记）

命中关键词（如「女主逆袭」）即**跳过选题环节**（S2a/S2b 不跑），直接用公式的 topic_card
进 ④剧本改写。**选题环节本身保持不动**，只有已收录的公式走这条捷径。

命令
----
    python genre_formula.py list
    python genre_formula.py match "帮我做一部女主逆袭的短剧"
    python genre_formula.py show --genre female-comeback            # 打印公式要点
    python genre_formula.py apply --genre female-comeback           # 落套用单（跳过选题）
    python genre_formula.py doc --genre female-comeback --write     # 生成 md 文档
    python genre_formula.py check --genre female-comeback           # 校验 blueprint 是否符合公式
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _common as C  # noqa: E402

REF_DIR = pathlib.Path(__file__).resolve().parents[1] / "references"
FORMULA_DIR = REF_DIR / "genre-formulas"
EXIT_GATE = 4          # 与全仓约定一致：4 = 关卡未过（等待确认 / 公式未套用）

# 压放呼吸循环（与 formula.pressure_release.cycle 对应：每 3 集内至少 1 压 1 放）
STAKE_CYCLE = ["压", "放", "悬"]


# ---------------------------------------------------------------------------
# 载入与路由
# ---------------------------------------------------------------------------
def formula_path(genre: str) -> pathlib.Path:
    return FORMULA_DIR / ("%s.json" % genre)


def list_formulas() -> list:
    out = []
    if not FORMULA_DIR.is_dir():
        return out
    for p in sorted(FORMULA_DIR.glob("*.json")):
        f = C.read_json(p)
        if isinstance(f, dict) and f.get("id"):
            out.append(f)
    return out


def load(genre: str) -> dict:
    f = C.read_json(formula_path(genre))
    if not isinstance(f, dict) or not f.get("id"):
        raise KeyError("未收录的题材公式：%s（已收录：%s）"
                       % (genre, "、".join(x["id"] for x in list_formulas()) or "无"))
    return f


def match_text(text: str) -> tuple:
    """关键词路由：返回 (formula_id, 命中的词) 或 (None, None)。

    取**最长命中**优先（避免「逆袭」压过「女主逆袭」这类更具体的词）。
    """
    t = str(text or "").strip().lower()
    if not t:
        return None, None
    best = (None, None)
    for f in list_formulas():
        for kw in f.get("keywords") or []:
            k = str(kw).lower()
            if k and k in t and (best[1] is None or len(k) > len(best[1])):
                best = (f["id"], kw)
    return best


# ---------------------------------------------------------------------------
# 公式套用：把公式映射到逐集（供 ④剧本改写 直接消费）
# ---------------------------------------------------------------------------
def _phase_ranges(f: dict) -> tuple:
    """解析 phases 的 ep_range → [(a, b)] 与基准总集数（最后阶段的 b）。"""
    ranges = []
    for ph in f.get("phases") or []:
        m = re.match(r"EP\s*(\d+)\s*[-–]\s*(\d+)", str(ph.get("ep_range") or ""))
        if m:
            ranges.append((int(m.group(1)), int(m.group(2))))
        else:
            m2 = re.match(r"EP\s*(\d+)\s*\+", str(ph.get("ep_range") or ""))
            ranges.append((int(m2.group(1)), 10 ** 6) if m2 else (1, 10 ** 6))
    base = ranges[-1][1] if ranges else 40
    if base >= 10 ** 6:                        # 形如 EP31+ 的写法：用倒数第二个端点兜底
        base = max((b for _, b in ranges if b < 10 ** 6), default=40)
    return ranges, base


def phase_of(f: dict, ep: int, n: int) -> dict:
    """按总集数**等比缩放**定位阶段（40 集时为原值；30 集时 EP1-2 / EP3-4 …）。"""
    ranges, base = _phase_ranges(f)
    phases = f.get("phases") or []
    for ph, (a, b) in zip(phases, ranges):
        lo = max(1, math.floor((a - 1) * n / base) + 1)
        hi = min(n, math.floor(b * n / base)) if b < 10 ** 6 else n
        hi = max(lo, hi)      # ★ 集数很短时也保证每个阶段至少 1 集（阶段不消失）
        if lo <= ep <= hi:
            return dict(ph, _lo=lo, _hi=hi)
    last = dict(phases[-1]) if phases else {}
    last.setdefault("_lo", 1)
    last["_hi"] = n
    return last


def episode_overlay(f: dict, ep: int, n: int) -> dict:
    """给出第 ep 集的公式化要求：打脸/等级/见证者/压放/里程碑/金句方向。"""
    ph = phase_of(f, ep, n)
    pid = ph.get("id") or "P?"
    qc = f.get("qc") or {}
    cadence = (qc.get("punch_cadence") or {}).get(pid, 2)
    level_floor = (qc.get("punch_min_level_per_phase") or {}).get(pid, 1)
    lo, hi = ph.get("_lo", 1), ph.get("_hi", n)

    # ★ 打脸节拍：**从阶段末集往前**每 cadence 集一次（阶段末集必打脸）；cadence=0 = 本阶段不打脸。
    #   从末集倒数符合剧作逻辑（每阶段收束处是高潮），也让触底段只保留末集的「首次小反击」。
    cadence = int(cadence or 0)
    if cadence <= 0:
        has_punch = False
    else:
        has_punch = cadence == 1 or ((hi - ep) % cadence == 0)
    # ★ 阶梯只升不降：按**全剧进度**定级（EP1→L1 … 末集→L4），再与阶段下限取大
    level = min(4, max(level_floor, 1 + int((ep - 1) / max(n - 1, 1) * 3.999)))
    ladder = next((x for x in (f.get("punch_ladder") or []) if x.get("level") == level), {})

    # ★ 压放呼吸：默认 3 集循环（压→放→悬）；公式可给阶段例外（如触底段全「压」）
    exc = ((f.get("pressure_release") or {}).get("phase_exceptions") or {})
    if pid in exc:
        stake = "压" if pid == "P1" else "扬"
    else:
        stake = STAKE_CYCLE[(ep - 1) % len(STAKE_CYCLE)]

    milestone = None
    every = int(qc.get("milestone_every") or 0)
    if every and ep % every == 0 and pid in ("P3", "P4", "P5"):
        pool = ((f.get("career_line") or {}).get("milestone_pool")) or []
        if pool:
            milestone = pool[(ep // every - 1) % len(pool)]

    quote = None
    bank = ((f.get("dialogue_policy") or {}).get("quote_bank")) or {}
    kinds = sorted(bank.keys())
    if kinds:
        kind = kinds[(ep - 1) % len(kinds)]
        items = bank[kind] or []
        if items:
            quote = "%s：%s" % (kind, items[(ep - 1) % len(items)])

    beats = "→".join(b.get("name", "") for b in (f.get("punch_five_beats") or []))
    return {
        "genre_phase": "%s %s" % (pid, ph.get("name") or ""),
        "stake": stake,
        "punch": ("L%d %s（%s）｜五拍：%s"
                  % (level, ladder.get("name", ""), ladder.get("who", ""), beats)) if has_punch else None,
        "punch_level": level if has_punch else 0,
        "witness": bool(has_punch and qc.get("witness_required")),
        "milestone": milestone,
        "quote": quote,
    }


def build_topic_card(f: dict, title=None, logline=None) -> dict:
    """公式的 topic_card（跳过选题时的定位卡替身）。"""
    card = json.loads(json.dumps(f.get("topic_card") or {}, ensure_ascii=False))
    if title:
        card["title"] = title
    if logline:
        card["logline"] = logline
    card["rank"] = 1
    card["_from_genre_formula"] = f.get("id")
    return card


def load_plan(ws) -> dict | None:
    """读工作区里已套用的公式单（有则 ④改写自动走公式模式）。"""
    obj = C.read_json(C.pdir(ws, C.GENRE_PLAN))
    if isinstance(obj, dict) and obj.get("genre"):
        try:
            obj["_formula"] = load(obj["genre"])
        except KeyError:
            obj["_formula"] = None
        return obj
    return None


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------
def cmd_list(a) -> int:
    fs = list_formulas()
    print(C.hr())
    print("题材公式库（%d 套）　目录：%s" % (len(fs), FORMULA_DIR))
    print(C.hr())
    for f in fs:
        print("● %s —— %s" % (f["id"], f.get("label")))
        print("  一句话：%s" % (f.get("one_liner") or "").replace("**", ""))
        print("  触发词：%s" % "、".join(f.get("keywords") or []))
        print("  跳过选题：%s ｜ 阶段：%d ｜ 打脸阶梯：%d 级"
              % ("是" if f.get("skip_topic") else "否",
                 len(f.get("phases") or []), len(f.get("punch_ladder") or [])))
    print()
    print("用法：match \"<用户输入>\" → apply --genre <id> → ④剧本改写（plan --genre <id>）")
    return 0


def cmd_match(a) -> int:
    gid, kw = match_text(a.text)
    if a.json:
        print(json.dumps({"genre": gid, "matched_by": kw}, ensure_ascii=False))
        return 0 if gid else 1
    if not gid:
        print("[MISS] 未命中任何题材公式（已收录：%s）"
              % "、".join(x["id"] for x in list_formulas()))
        print("       按常规流程走：① 调研 → ② 破拆 → ③ 选题 → ④ 剧本改写。")
        return 1
    f = load(gid)
    print("[HIT ] 命中公式：%s（由关键词「%s」命中）" % (gid, kw))
    print("       %s" % f.get("label"))
    print("       跳过选题：%s —— %s" % ("是" if f.get("skip_topic") else "否",
                                        (f.get("skip_topic_note") or "")[:80]))
    print("       下一步：python genre_formula.py --workspace <ws> apply --genre %s" % gid)
    return 0


def cmd_show(a) -> int:
    f = load(a.genre)
    print(C.hr())
    print("题材公式 · %s（%s）" % (f.get("label"), f["id"]))
    print(C.hr())
    print("【引擎】%s" % ((f.get("engine") or {}).get("formula", "")))
    print("\n【根本原因与对策】")
    for i, rc in enumerate(f.get("root_causes") or [], 1):
        print("  %d. %s" % (i, rc.get("cause")))
        print("     症状：%s" % rc.get("symptom"))
        print("     实测：%s" % rc.get("evidence"))
        print("     对策：%s" % rc.get("fix"))
    print("\n【阶段】")
    for ph in f.get("phases") or []:
        print("  %s %s（%s）：%s" % (ph.get("id"), ph.get("name"), ph.get("ep_range"), ph.get("goal")))
    print("\n【打脸阶梯】")
    for l in f.get("punch_ladder") or []:
        print("  L%d %s（%s）｜%s｜%s" % (l.get("level"), l.get("name"), l.get("who"),
                                          l.get("role"), l.get("freq")))
    print("\n【五拍打脸法】%s" % "；".join(
        "%d %s：%s" % (b.get("beat"), b.get("name"), b.get("content")) for b in f.get("punch_five_beats") or []))
    print("\n【前 3 集钩子公式】")
    for k, v in (f.get("hook_formula") or {}).items():
        if k in ("name",):
            continue
        print("  %s：%s" % (k, v))
    print("\n【QC 阈值】%s" % json.dumps(f.get("qc") or {}, ensure_ascii=False))
    return 0


def cmd_apply(a) -> int:
    ws = C.resolve_ws(a.workspace)
    f = load(a.genre)
    card = build_topic_card(f, a.title, a.logline)
    plan_obj = {
        "generated_at": C.now_iso(), "module": "S2.5-genre-formula", "schema_version": 1,
        "genre": f["id"], "label": f.get("label"), "version": f.get("version"),
        "matched_by": a.matched_by,
        "skip_topic": bool(f.get("skip_topic")),
        "skip_topic_note": f.get("skip_topic_note"),
        "topic_card": card,
        "engine": f.get("engine"), "phases": f.get("phases"),
        "punch_ladder": f.get("punch_ladder"), "punch_five_beats": f.get("punch_five_beats"),
        "hook_formula": f.get("hook_formula"), "qc": f.get("qc"),
        "redlines": f.get("redlines"),
        "note": "本单由 genre_formula.py apply 落地；④剧本改写读它即自动套用公式并跳过选题。",
    }
    p = C.write_json(C.pdir(ws, C.GENRE_PLAN), plan_obj)
    print(C.hr())
    print("题材公式已套用：%s" % f.get("label"))
    print(C.hr())
    print("  落点      : %s" % p)
    print("  跳过选题  : %s" % ("是（S2a/S2b 不跑）" if plan_obj["skip_topic"] else "否"))
    print("  引擎      : %s" % ((f.get("engine") or {}).get("formula", "")))
    print("  阶段      : %s" % " → ".join("%s %s" % (p2.get("id"), p2.get("name"))
                                            for p2 in f.get("phases") or []))
    print("  打脸阶梯  : %s" % " → ".join("L%d %s" % (l.get("level"), l.get("name"))
                                            for l in f.get("punch_ladder") or []))
    print("  QC        : %s" % json.dumps(f.get("qc") or {}, ensure_ascii=False))
    print()
    print("  下一步：python stages/creation/scripts/script_rewrite.py --workspace <ws> "
          "plan --genre %s" % f["id"])
    return 0


def cmd_check(a) -> int:
    ws = C.resolve_ws(a.workspace)
    f = load(a.genre)
    bp = C.read_json(C.pdir(ws, C.BLUEPRINT))
    if not isinstance(bp, dict) or not bp.get("episodes"):
        print("[FAIL] 找不到集数规划：%s（先跑 script_rewrite.py plan）" % C.pdir(ws, C.BLUEPRINT))
        return 2

    eps = bp["episodes"]
    n = len(eps)
    qc = f.get("qc") or {}
    fails, warns, oks = [], [], []
    cadence = qc.get("punch_cadence") or {}
    floor = qc.get("punch_min_level_per_phase") or {}

    # 1) 打脸节拍与阶梯
    bad_punch, bad_level, bad_ladder = [], [], []
    prev_level = 0
    for e in eps:
        ep = int(e.get("ep") or 0)
        ph = phase_of(f, ep, n)
        pid = ph.get("id")
        lo = ph.get("_lo", 1)
        expect = int(cadence.get(pid, 0) or 0)
        hi = ph.get("_hi", n)
        need = expect > 0 and (expect == 1 or (hi - ep) % expect == 0)
        if need and not e.get("punch"):
            bad_punch.append(ep)
        if e.get("punch"):
            lv = int(e.get("punch_level") or 0)
            if lv < int(floor.get(pid, 1)):
                bad_level.append("EP%d L%d<%d" % (ep, lv, floor.get(pid, 1)))
            if lv < prev_level:
                bad_ladder.append("EP%d 阶梯回落 L%d→L%d" % (ep, lv, prev_level))
            prev_level = max(prev_level, lv)
    (oks if not bad_punch else fails).append(
        ("打脸节拍（每阶段按 %s 集一次）" % json.dumps(cadence, ensure_ascii=False),
         "缺打脸集：%s" % bad_punch if bad_punch else "全阶段达标"))
    (oks if not bad_level else fails).append(
        ("打脸等级下限", "越界：%s" % bad_level if bad_level else "达标"))
    (oks if not bad_ladder else warns).append(
        ("打脸阶梯只升不降", "回落：%s" % bad_ladder if bad_ladder else "单调不降"))

    # 2) 见证者
    no_wit = [int(e.get("ep")) for e in eps if e.get("punch") and not e.get("witness")]
    (oks if not no_wit else fails).append(("打脸必须有见证者", "缺见证：%s" % no_wit if no_wit else "达标"))

    # 3) 金句
    no_quote = [int(e.get("ep")) for e in eps if not e.get("quote")]
    (oks if not no_quote else fails).append(
        ("每集至少 1 句金句（打脸集）", "缺失：%s" % no_quote[:10] if no_quote else "达标"))

    # 4) 事业线里程碑
    every = int(qc.get("milestone_every") or 0)
    if every:
        miss = [int(e.get("ep")) for e in eps
                if phase_of(f, int(e.get("ep")), n).get("id") in ("P3", "P4", "P5")
                and int(e.get("ep")) % every == 0 and not e.get("milestone")]
        (oks if not miss else warns).append(("事业线里程碑每 %d 集" % every,
                                             "缺失：%s" % miss if miss else "达标"))

    # 5) 前 3 集钩子不重复 + 黄金 3 秒
    h3 = [e.get("hook_type") for e in eps[:3]]
    (oks if len(set(h3)) == 3 else fails).append(("EP1-3 钩子类型互不相同", str(h3)))
    ep1 = eps[0]
    golden = "黄金 3 秒" in str(ep1.get("cold_open") or "") or "golden" in str(ep1.get("cold_open") or "").lower()
    (oks if golden else fails).append(("EP1 黄金 3 秒", str(ep1.get("cold_open"))[:40]))

    # 6) 压放呼吸（公式声明的阶段例外不受此约束，如触底段允许连续「压」）
    exc_ids = set(k for k in ((f.get("pressure_release") or {}).get("phase_exceptions") or {})
                  if k in ("P1", "P2", "P3", "P4", "P5"))
    bad_breath = []
    for i in range(0, len(eps) - 2):
        win = eps[i:i + 3]
        if any(phase_of(f, int(e.get("ep")), n).get("id") in exc_ids for e in win):
            continue
        w = [e.get("stake") for e in win]
        if len(set(w)) == 1:
            bad_breath.append("EP%d-%d 连续「%s」" % (eps[i]["ep"], eps[i + 2]["ep"], w[0]))
    (oks if not bad_breath else warns).append(
        ("压放呼吸（每 3 集至少 1 压 1 放）", "；".join(bad_breath[:4]) if bad_breath else "达标"))

    print(C.hr())
    print("题材公式结构校验 · %s" % f.get("label"))
    print(C.hr())
    print("  规划：%s（%d 集）" % (C.pdir(ws, C.BLUEPRINT), n))
    for k, v in oks:
        print("  [OK  ] %-34s %s" % (k, v))
    for k, v in warns:
        print("  [WARN] %-34s %s" % (k, v))
    for k, v in fails:
        print("  [FAIL] %-34s %s" % (k, v))
    print()
    if fails:
        print("[FAIL] 未过公式 QC（%d 项）：%s" % (len(fails), "；".join(k for k, _ in fails)))
        return 1
    print("[OK] 通过公式 QC%s" % ("（含 %d 项警告）" % len(warns) if warns else ""))
    return 0


# ---------------------------------------------------------------------------
# 由 JSON 生成人读/LLM 读文档
# ---------------------------------------------------------------------------
def render_doc(f: dict) -> str:
    L = []
    A = L.append
    A("# 题材公式 · %s" % f.get("label"))
    A("")
    A("> ⚠️ 本文件由 `genre_formula.py doc --genre %s --write` **自动生成**，请勿手改；"
      "真源是同目录的 `%s.json`。" % (f["id"], f["id"]))
    A("")
    A("> 一句话：%s" % f.get("one_liner"))
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A("| 公式 ID | `%s` |" % f["id"])
    A("| 触发关键词 | %s |" % "、".join(f.get("keywords") or []))
    A("| 是否跳过选题 | **%s** |" % ("是" if f.get("skip_topic") else "否"))
    A("| 引擎 | `%s` |" % ((f.get("engine") or {}).get("formula", "")))
    A("| 来源 | %s |" % f.get("source"))
    A("")
    if f.get("skip_topic_note"):
        A("> %s" % f["skip_topic_note"])
        A("")

    A("## 一、为什么剧本会「简单、不爽」（根本原因 → 对策）")
    A("")
    A("| # | 根本原因 | 症状 | 实测证据 | 对策 |")
    A("|---|---|---|---|---|")
    for i, rc in enumerate(f.get("root_causes") or [], 1):
        A("| %d | **%s** | %s | %s | %s |" % (i, rc.get("cause"), rc.get("symptom"),
                                               rc.get("evidence"), rc.get("fix")))
    A("")

    A("## 二、引擎与阶段")
    A("")
    A("- **引擎**：%s" % ((f.get("engine") or {}).get("formula", "")))
    A("- **铁律**：%s" % ((f.get("engine") or {}).get("rule", "")))
    A("")
    A("| 阶段 | 名称 | 集范围 | 目标 | 必须做到 | 禁止 |")
    A("|---|---|---|---|---|---|")
    for ph in f.get("phases") or []:
        A("| %s | %s | %s | %s | %s | %s |"
          % (ph.get("id"), ph.get("name"), ph.get("ep_range"), ph.get("goal"),
             "<br>".join("- " + x for x in ph.get("must") or []),
             "<br>".join("- " + x for x in ph.get("forbid") or [])))
    A("")

    A("## 三、打脸阶梯（对手必须一级一级升级）")
    A("")
    A("| 级别 | 名称 | 对手类型 | 作用 | 频率 |")
    A("|---|---|---|---|---|")
    for l in f.get("punch_ladder") or []:
        A("| L%d | %s | %s | %s | %s |" % (l.get("level"), l.get("name"), l.get("who"),
                                           l.get("role"), l.get("freq")))
    A("")
    A("## 四、五拍打脸法（缺一拍即判定「打脸未完成」）")
    A("")
    for b in f.get("punch_five_beats") or []:
        A("%d. **%s** —— %s" % (b.get("beat"), b.get("name"), b.get("content")))
    A("")
    A("> %s" % f.get("punch_five_beats_rule"))
    A("")

    A("## 五、压放呼吸（情绪节奏）")
    A("")
    pr = f.get("pressure_release") or {}
    A("- **循环**：%s" % pr.get("cycle"))
    A("- **压**：%s" % "、".join(pr.get("pressure_types") or []))
    A("- **放**：%s" % "、".join(pr.get("release_types") or []))
    A("- **禁止**：%s" % pr.get("forbid"))
    A("")

    A("## 六、事业线（可信度来源）")
    A("")
    cl = f.get("career_line") or {}
    A("- **作用**：%s" % cl.get("role"))
    A("- **规则**：%s" % cl.get("rule"))
    A("- **里程碑池**：%s" % "、".join(cl.get("milestone_pool") or []))
    A("")

    A("## 七、前 3 集爆款钩子公式")
    A("")
    hf = f.get("hook_formula") or {}
    for k, v in hf.items():
        if k == "name":
            A("### %s" % v)
            A("")
            continue
        A("- **%s**：%s" % (k, v))
    A("")

    A("## 八、台词大胆原则与金句库")
    A("")
    dp = f.get("dialogue_policy") or {}
    A("- **原则**：%s" % dp.get("principle"))
    A("- **允许更大胆**：%s" % "；".join(dp.get("bold_allowed") or []))
    A("- **禁止**：%s" % "；".join(dp.get("forbidden") or []))
    A("- **金句规则**：%s" % "；".join(dp.get("quote_rules") or []))
    A("")
    A("| 类型 | 可用句 |")
    A("|---|---|")
    for k, v in (dp.get("quote_bank") or {}).items():
        A("| %s | %s |" % (k, " / ".join(v)))
    A("")

    A("## 九、质量控制标准（机器校验口径）")
    A("")
    A("| 检查项 | 阈值 |")
    A("|---|---|")
    for k, v in (f.get("qc") or {}).items():
        A("| `%s` | %s |" % (k, json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v))
    A("")
    A("```bash")
    A("# 一条命令校验整份规划")
    A((f.get("qc") or {}).get("check_cmd", ""))
    A("```")
    A("")

    A("## 十、红线（违反即返工）")
    A("")
    for x in f.get("redlines") or []:
        A("- %s" % x)
    A("")
    A("## 十一、给 LLM 的写作指引")
    A("")
    for x in f.get("llm_guide") or []:
        A("- %s" % x)
    A("")
    A("---")
    A("")
    A("*本公式可扩展：新增题材只需在 `references/genre-formulas/` 放一份同名 JSON，"
      "再跑 `genre_formula.py doc --genre <id> --write` 生成文档即可，无需改代码。*")
    return "\n".join(L) + "\n"


def cmd_doc(a) -> int:
    f = load(a.genre)
    text = render_doc(f)
    if a.write:
        p = C.write_text(formula_path(a.genre).with_suffix(".md"), text)
        print("[OK] 已生成文档：%s（%d 行）" % (p, text.count("\n")))
        return 0
    print(text)
    return 0


# ---------------------------------------------------------------------------
def main() -> int:
    C.harden_stdio()
    ap = argparse.ArgumentParser(description="固定题材框架：关键词路由 / 公式套用 / 结构校验")
    ap.add_argument("--workspace", default=None)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("list", help="列出全部题材公式")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("match", help="按关键词匹配公式")
    p.add_argument("text")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_match)

    p = sub.add_parser("show", help="打印公式要点（根本原因/阶梯/五拍/钩子）")
    p.add_argument("--genre", required=True)
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("apply", help="把公式落地为工作区套用单（跳过选题）")
    p.add_argument("--genre", required=True)
    p.add_argument("--title", default=None)
    p.add_argument("--logline", default=None)
    p.add_argument("--matched-by", default=None, help="命中的关键词（可追溯）")
    p.set_defaults(fn=cmd_apply)

    p = sub.add_parser("check", help="校验 blueprint 是否符合公式 QC")
    p.add_argument("--genre", required=True)
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("doc", help="由 JSON 生成 md 文档")
    p.add_argument("--genre", required=True)
    p.add_argument("--write", action="store_true")
    p.set_defaults(fn=cmd_doc)

    a = ap.parse_args()
    if not getattr(a, "fn", None):
        return cmd_list(a)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
