#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""script_rewrite.py —— 第④部分「剧本改写」

职责（**只搭规划骨架与红线，正文交 LLM 按 prompt 填**）
-------------------------------------------------------
对应交付的第四部分：**将一部「很颠」的西语短剧剧本改写为完整 N 集规划（默认 60 集），
每集须包含核心冲突、剧情反转与钩子设计；整体节奏紧凑、钩子密集、爽点高频。**

本脚本做三件确定性的事：

1. **集数蓝图 `plan`**：把定位卡（③）落到**逐集规划**——每集给出
   `核心冲突 / 剧情反转 / 钩子 / 钩子类型 / 情绪走向 / 节拍说明`，
   并植入**爆点节拍**（中型爆点每 6 集、大型爆点每 15 集、集尾钩子短收）。
   源剧本（`source-script.md`，"很颠"的原稿）会先被登记与体检。
2. **FFS 骨架 `build`**：按 `docs/creation/ffs-spec.md` 生成某集的文件头 + N 个空镜头块，
   字段与顺序合规，LLM 只需替换占位符。
3. **结构自检 `lint`**：对填充后的剧本做确定性检查（头字段/镜头块/禁字段/DUR/时长口径/
   镜头数/台词长度），把错误挡在 `tools/cli.py check` 之前。

输入：`creation/02-positioning/positioning.json`（rank N 定位卡）+ `creation/brief.json`
     + `creation/03-script/source-script.md`（可选，待改写的源剧本）
     + `creation/01-breakdown/breakdown.json`（可选，拆解结论）
     + `creation/03-script/reference-structure.json`（可选，对标结构）
输出：`creation/03-script/blueprint.json` + `blueprint.md` + `script-brief.md`（+ `skeleton_ep_XX.md`）

命令
----
    python script_rewrite.py plan [--rank 1] [--episodes 60] [--emit-brief]
    python script_rewrite.py build --ep 1 [--shots 8] [--emit]
    python script_rewrite.py lint --ep 1
    python script_rewrite.py ref-template
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _common as C  # noqa: E402

# ---- STYLE LOCK 预设（与 docs/creation/stylelock-presets.md 一致） ----------
BASE_LOCK = "no watermark, no subtitle, no text overlay, no logo"
STYLE_LOCKS = {
    "都市情感": "webtoon-adapted live-action style, soft warm lighting, pastel color palette, "
                "detailed character design, gentle bokeh in close-ups, 2.39:1 widescreen, " + BASE_LOCK,
    "逆袭": "webtoon-adapted live-action style, sleek modern interiors, sharp contrast, "
            "high-fashion styling, cool steel-blue accents, 2.39:1 widescreen, " + BASE_LOCK,
    "职场": "webtoon-adapted live-action style, sleek modern interiors, sharp contrast, "
            "high-fashion styling, cool steel-blue accents, 2.39:1 widescreen, " + BASE_LOCK,
    "热血": "webtoon-adapted live-action style, dynamic action framing, dramatic rim lighting, "
            "bold inking, dust particles in fight scenes, 2.39:1 widescreen, " + BASE_LOCK,
    "脑洞": "webtoon-adapted live-action style, desaturated palette with neon accents, uncanny detail, "
            "subtle lens distortion, 2.39:1 widescreen, " + BASE_LOCK,
    "奇幻": "webtoon-adapted dark fantasy style, moonlit cool palette with blood-red accents, "
            "ornate gothic interiors, sharp inking, fur and fabric detail, 2.39:1 widescreen, " + BASE_LOCK,
    "狼人": "webtoon-adapted dark fantasy style, moonlit cool palette with blood-red accents, "
            "ornate gothic interiors, sharp inking, fur and fabric detail, 2.39:1 widescreen, " + BASE_LOCK,
    "霸总": "webtoon-adapted live-action style, sleek modern interiors, sharp contrast, "
            "high-fashion styling, cool steel-blue accents, 2.39:1 widescreen, " + BASE_LOCK,
    "_default": "webtoon-adapted live-action style, cinematic framing, controlled contrast, "
                "detailed character design, 2.39:1 widescreen, " + BASE_LOCK,
}

DEFAULT_EPISODES = 30        # ★ 2026-10-16 用户拍板：由 60 降为 30（前 3 集留存攻坚 + 投入产出比）
ACT_SPLIT = [("起", 0.15), ("承", 0.45), ("转", 0.75), ("合", 1.0)]
HOOK_TYPES = ["身份悬念", "危机逼近", "关系反转", "秘密揭露", "选择困境", "意外登场", "代价预告"]
MID_BOOM_EVERY = 3           # 中型爆点（30 集口径；60 集口径为 6，**密度保持一致**）
BIG_BOOM_EVERY = 8           # 大型爆点（30 集口径；60 集口径为 15，**密度保持一致**）

# ===========================================================================
# ★ 留存率导向的剧本结构（2026-10-16 新增）
# ---------------------------------------------------------------------------
# 背景：用户实测几部剧「内容吸引度不够」—— 症状是前几集没能抓住人、每集中段平。
# 因此本层把「留存结构」从"写作建议"升级为**可机器校验的硬结构**：
#
#   每集必含四件套（缺一即返工）：
#     ① cold_open      开场钩子 —— 前 3-5 秒内出现「异常/冲突/悬念」之一
#                       EP1 为**黄金 3 秒**：必须把全剧最高冲突前置，禁止背景铺垫开场
#     ② core_conflict  核心冲突 —— 本集「谁 vs 谁 vs 什么」
#     ③ reversal       一次反转 —— 且必须改变力量对比或信息格局（不能只是情绪起伏）
#     ④ hook           集尾钩子 —— 让人必须点下一集；**类型不得与前两集连续重复**
#
#   留存三段（retention_role）：
#     EP1-3   「拉新攻坚」三连强钩子链，禁止出现铺垫集、禁止慢热
#     EP4-10  「留存活口」每集至少一次「小爽」落点，爆点密度最高
#     EP11+   「转化与收束」按爆点节拍推进，收尾前 2 集必须再拉一次强钩子
# ===========================================================================
RETENTION_ROLES = [
    (3, "拉新攻坚", "三连强钩子链；EP1 前 3 秒必须出现最高冲突，禁止背景铺垫开场"),
    (10, "留存活口", "每集至少一次「小爽」落点；中段不得出现连续两集无爆点"),
    (10 ** 9, "转化与收束", "按爆点节拍推进；收尾前 2 集必须再拉一次强钩子，留悬念收束"),
]

# 开场钩子（cold_open）句库：按留存段分配，保证前 3 秒有信息量
COLD_OPEN_BANK = {
    "拉新攻坚": [
        "冷开场直接抛出本集最尖锐的矛盾，主角处境一句话说清",
        "开场即冲突现场，先给结果再倒叙成因（3 秒内出画面）",
        "开场用一句反常识台词制造认知落差",
    ],
    "留存活口": [
        "开场承接上集钩子，10 秒内给出新变量",
        "开场以对手视角开场，让观众先看到危机全貌",
        "开场用一次小胜/小爽落点回报观众耐心",
    ],
    "转化与收束": [
        "开场给出倒计时或代价预告，明确终局临近",
        "开场回收一条早期伏笔，制造「原来如此」的满足感",
        "开场让主角主动出击，扭转被动局面",
    ],
}


def _retention_role(ep: int) -> tuple:
    """返回 (留存段名, 段内说明)。"""
    for upto, name, note in RETENTION_ROLES:
        if ep <= upto:
            return name, note
    return RETENTION_ROLES[-1][1], RETENTION_ROLES[-1][2]


CONFLICT_BANK = {
    "起": ["身份错位引发冲突", "至亲/资源被夺", "被误解并驱逐", "契约/误认开局"],
    "承": ["对手步步施压", "旧敌卷土重来", "真相碎片浮现", "信任开始崩塌"],
    "转": ["真凶身份揭露", "主角力量/身份觉醒", "盟友背叛", "公开对质"],
    "合": ["终极对决", "真相大白与清算", "代价与和解", "双强联手收官"],
}
REVERSAL_BANK = {
    "起": ["看似劣势实为伏笔", "救命恩人身份可疑", "主角底牌尚未亮出"],
    "承": ["示弱者才是操盘手", "关键证据被人为篡改", "最亲近者递出关键线索"],
    "转": ["背叛者竟是保护者", "复仇目标并非真凶", "主角身份高于对手"],
    "合": ["反派自曝致命破绽", "牺牲换来全局翻盘", "旧仇握手结成同盟"],
}
HOOK_TEMPLATE = {
    "身份悬念": "揭出主角身世/血脉的一角，但未说全",
    "危机逼近": "对手的下一步已经启动，倒计时开始",
    "关系反转": "最信任的人态度突然反转",
    "秘密揭露": "被隐藏多年的秘密浮出水面",
    "选择困境": "主角被迫在两个人/两件事之间选一个",
    "意外登场": "关键人物在此刻意外登场",
    "代价预告": "翻盘的代价被明确提出",
}
EMOTION_CYCLE = ["压抑", "期待", "小爽", "悬", "大爽", "暖", "虐", "燃"]
HOOK_CHAIN_FIRST3 = ["身份悬念", "危机逼近", "秘密揭露"]

REDLINE_HARD = [
    "**不复刻**：不得照搬源剧本或任何对标剧的台词、镜序、角色名与专属设定，只借鉴结构与爽点机制。",
    "**不重叠**：同一批选题之间不得复用同一核心冲突结构。",
    "**反套路可验证**：若用「反套路」作卖点，必须写明反的是哪个具体套路（如「无替身梗、无失忆梗」）。",
    "**本土化**：台词为口语西语（拉美通用），避免翻译腔；文化元素须为拉美/西语观众可代入（家庭、阶层、都市、足球等）。",
    "**节奏铁律**：每集必须同时具备「核心冲突 + 一次反转 + 集尾钩子」；整体钩子密集、爽点高频。",
]

FFS_HEADER_ORDER = ["TITLE", "EP", "PLATFORM", "STYLE LOCK", "CHAR", "SCENE"]
FFS_SHOT_FIELDS = ["SHOT", "LOCATION", "TIME", "CHAR", "ACTION", "DIALOG", "VO", "SFX"]
FFS_FORBIDDEN = ["MOOD", "CAMERA", "EMOTION", "MUSIC", "LIGHT"]
SHOT_MIN, SHOT_MAX = 6, 15
DUR_MIN, DUR_MAX = 4, 15
TOTAL_MIN, TOTAL_MAX = 90, 120


# ---------------------------------------------------------------------------
# 源剧本登记与体检
# ---------------------------------------------------------------------------
def inspect_source(ws: pathlib.Path) -> dict:
    p = C.pdir(ws, C.SOURCE_SCRIPT)
    text = C.read_text(p)
    if not text:
        return {"present": False, "path": str(p),
                "note": "未找到源剧本（可放入 %s 供改写参考）" % p}
    acts = re.findall(r"(?m)^\s*(?:第\s*[一二三四五六七八九十\d]+\s*幕|Act\s*\d+|Part\s*\d+)", text)
    scenes = re.findall(r"(?m)^\s*(?:INT\.|EXT\.|内景|外景|场\d+)", text)
    return {"present": True, "path": str(p), "chars": len(text),
            "lines": text.count("\n") + 1,
            "acts_detected": [a.strip() for a in acts][:12],
            "scenes_detected": len(scenes),
            "note": "源剧本已登记；改写时**保留其核心人设与设定魅力**，仅重排结构、加密钩子。"}


# ---------------------------------------------------------------------------
# 第④部分核心：逐集规划
# ---------------------------------------------------------------------------
def _act_of(i: int, n: int) -> str:
    r = i / max(n, 1)
    for act, upto in ACT_SPLIT:
        if r <= upto:
            return act
    return ACT_SPLIT[-1][0]


def _pick(bank: list, i: int) -> str:
    return bank[(i - 1) % len(bank)]


def plan(ws: pathlib.Path, rank: int = 1, episodes: int | None = None, dry: bool = False) -> int:
    pos_obj = C.read_json(C.pdir(ws, C.POSITIONING))
    if not isinstance(pos_obj, dict) or not pos_obj.get("selected"):
        print("[FAIL] 找不到③定位卡：%s（先跑 hit_selection.py select）" % C.pdir(ws, C.POSITIONING))
        return 2
    card = next((x for x in pos_obj["selected"] if x.get("rank") == rank), None)
    if not card:
        print("[FAIL] 定位卡里没有 rank=%d" % rank)
        return 2
    brief = C.read_json(C.pdir(ws, C.BRIEF)) or {}
    bd = C.read_json(C.pdir(ws, C.BREAKDOWN)) or {}
    src = inspect_source(ws)
    n = int(episodes or brief.get("ep_total") or DEFAULT_EPISODES)
    if n < 6:
        print("[FAIL] 集数过少（%d）：短剧规划至少 6 集" % n)
        return 2

    style = _pick_style_lock(str(card.get("genre_direction")))
    platform = (card.get("audience_profile") or {}).get("platform") or brief.get("platform") or "TikTok"

    episodes_list = []
    for i in range(1, n + 1):
        act = _act_of(i, n)
        hook_type = HOOK_CHAIN_FIRST3[i - 1] if i <= 3 else HOOK_TYPES[(i - 1) % len(HOOK_TYPES)]
        boom = ("big" if i % BIG_BOOM_EVERY == 0 else ("mid" if i % MID_BOOM_EVERY == 0 else "none"))
        r_name, r_note = _retention_role(i)
        cold = COLD_OPEN_BANK[r_name][(i - 1) % len(COLD_OPEN_BANK[r_name])]
        if i == 1:
            cold = "★黄金 3 秒：把全剧最高冲突前置到开场（禁止背景铺垫、禁止慢热）"
        episodes_list.append({
            "ep": i, "act": act,
            "retention_role": r_name,
            "cold_open": cold,
            "core_conflict": "%s：%s" % (_pick(CONFLICT_BANK[act], i), card.get("conflict") or "主线对抗"),
            "reversal": _pick(REVERSAL_BANK[act], i),
            "hook_type": hook_type,
            "hook": "%s（%s）" % (HOOK_TEMPLATE[hook_type], card.get("logline") or "主线钩子"),
            "emotion": EMOTION_CYCLE[(i - 1) % len(EMOTION_CYCLE)],
            "beat_note": ("★大型爆点集" if boom == "big" else ("中型爆点集" if boom == "mid" else "常规推进")),
            "boom": boom,
        })

    hook_chain = [{"ep": e["ep"], "type": e["hook_type"], "hook": e["hook"]}
                  for e in episodes_list[:3]]
    act_structure = [{"act": a, "ep_range": "%d-%d" % (int(prev * n) + 1, int(upto * n)),
                      "share_pct": round((upto - prev) * 100, 1)}
                     for (a, upto), prev in zip(ACT_SPLIT, [0.0] + [x[1] for x in ACT_SPLIT[:-1]])]

    obj = {
        "generated_at": C.now_iso(), "module": "S3-script-rewrite-plan", "schema_version": 1,
        "title": card.get("title"), "title_es": card.get("title_es"),
        "market": (card.get("audience_profile") or {}).get("market") or brief.get("market"),
        "platform": platform, "form": brief.get("form") or "AI漫剧",
        "total_ep": n, "ep_seconds": brief.get("ep_seconds") or 90,
        "style_lock": style, "rank": rank,
        "source_script": src,
        "positioning_digest": {
            "logline": card.get("logline"), "conflict": card.get("conflict"),
            "audience_profile": card.get("audience_profile"),
            "genre_direction": card.get("genre_direction"),
            "differentiation_selling_points": card.get("differentiation_selling_points"),
        },
        "act_structure": act_structure, "hook_chain": hook_chain,
        "boom_cadence": {"mid_every": MID_BOOM_EVERY, "big_every": BIG_BOOM_EVERY,
                         "mid_count": sum(1 for e in episodes_list if e["boom"] == "mid"),
                         "big_count": sum(1 for e in episodes_list if e["boom"] == "big")},
        # ★ 留存率导向结构（2026-10-16）：四件套 + 三段留存 + 校验口径
        "retention_plan": {
            "framework": ["cold_open（开场钩子）", "core_conflict（核心冲突）",
                          "reversal（一次反转，须改变力量对比或信息格局）", "hook（集尾钩子）"],
            "roles": [{"role": n, "ep_upto": u, "note": note}
                      for u, n, note in RETENTION_ROLES if u < 10 ** 9]
                     + [{"role": RETENTION_ROLES[-1][1], "ep_upto": None, "note": RETENTION_ROLES[-1][2]}],
            "ep1_golden_3s": "EP1 前 3 秒必须出现全剧最高冲突（禁止铺垫开场）",
            "hook_no_repeat_window": 2,   # 钩子类型不得与前 2 集连续重复
            "rationale": "用户实测多部剧「内容吸引度不够」；本结构把留存要求变成可机器校验的硬字段。",
        },
        "episodes": episodes_list,
        "hot_tropes": [t["name"] for t in (bd.get("tropes") or []) if t.get("count", 0) >= 2][:6],
        "differentiation_redlines": REDLINE_HARD + list(card.get("differentiation_redlines") or []),
        "note": "本文件是④改写的**结构规划**；每集正文由 LLM 按 prompts/s3-script-rewrite.md 填。",
    }

    print(C.hr())
    print("④ 剧本改写 · 集数规划（源剧本改写为 %d 集）" % n)
    print(C.hr())
    print("  选题      : #%d %s（%s）" % (rank, card.get("title"), card.get("genre_direction")))
    print("  平台/形态 : %s / %s" % (platform, obj["form"]))
    print("  源剧本    : %s" % ("已登记（%d 字 / 场景 %d）" % (src.get("chars", 0), src.get("scenes_detected", 0))
                              if src.get("present") else "未提供（按定位卡原创结构）"))
    print("  幕结构    : %s" % "；".join("%s EP%s" % (a["act"], a["ep_range"]) for a in act_structure))
    print("  钩子链    : %s" % " → ".join("%s(%s)" % (h["type"], h["ep"]) for h in hook_chain))
    print("  留存结构  : 每集四件套（开场钩子 + 核心冲突 + 一次反转 + 集尾钩子）")
    print("              三段：%s" % "；".join(
        "EP1-%s %s" % (r["ep_upto"], r["role"]) for r in obj["retention_plan"]["roles"][:2])
        + "；EP11+ %s" % obj["retention_plan"]["roles"][-1]["role"])
    print("              EP1 = %s" % obj["retention_plan"]["ep1_golden_3s"])
    print("  爆点节拍  : 中型 %d 次（每 %d 集）/ 大型 %d 次（每 %d 集）"
          % (obj["boom_cadence"]["mid_count"], MID_BOOM_EVERY,
             obj["boom_cadence"]["big_count"], BIG_BOOM_EVERY))
    print("  前 8 集预览：")
    for e in episodes_list[:8]:
        print("    EP%02d [%s] %s｜反转：%s｜钩子(%s)：%s"
              % (e["ep"], e["act"], e["core_conflict"][:22], e["reversal"][:16],
                 e["hook_type"], e["hook"][:24]))
    print("    … 共 %d 集，逐集见 blueprint.json / blueprint.md" % n)

    if dry:
        print("[dry] 未落盘。")
        return 0
    p1 = C.write_json(C.pdir(ws, C.BLUEPRINT), obj)
    p2 = C.write_text(C.pdir(ws, "creation/03-script/blueprint.md"), render_blueprint_md(obj))
    p3 = C.write_text(C.pdir(ws, "creation/03-script/script-brief.md"), render_script_brief(obj))
    print("\n[OK] 已写出：\n     %s\n     %s\n     %s" % (p1, p2, p3))
    print("     下一步：LLM 按 prompts/s3-script-rewrite.md 写第 1 集 → "
          "script_rewrite.py build --ep 1 --emit → lint --ep 1")
    return 0


def _pick_style_lock(track: str) -> str:
    for k, v in STYLE_LOCKS.items():
        if k != "_default" and k in str(track):
            return v
    return STYLE_LOCKS["_default"]


def render_blueprint_md(o: dict) -> str:
    L = ["# ④ 剧本改写 · 集数规划（%s）" % o["generated_at"][:19], "",
         "- 剧名：%s（%s）　市场：%s　平台：%s　形态：%s"
         % (o["title"], o.get("title_es") or "-", o["market"], o["platform"], o["form"]),
         "- 集数：%d 集 × %d 秒　STYLE LOCK：`%s`" % (o["total_ep"], o["ep_seconds"], o["style_lock"][:50] + "…"),
         "- 源剧本：%s" % (o["source_script"].get("note") if o["source_script"].get("present")
                          else "未提供（按定位卡原创结构）"),
         "- 爆点节拍：中型 %d 次 / 大型 %d 次" % (o["boom_cadence"]["mid_count"], o["boom_cadence"]["big_count"]),
         "", "## 幕结构与钩子链", ""]
    L += ["| 幕 | 集数区间 | 占比 |", "|---|---|---|"]
    L += ["| %s | EP%s | %.1f%% |" % (a["act"], a["ep_range"], a["share_pct"]) for a in o["act_structure"]]
    L += ["", "| 集 | 钩子类型 | 钩子 |", "|---|---|---|"]
    L += ["| EP%d | %s | %s |" % (h["ep"], h["type"], h["hook"]) for h in o["hook_chain"]]
    L += ["", "## 逐集规划（核心冲突 / 反转 / 钩子 / 情绪）", "",
          "| 集 | 幕 | 核心冲突 | 剧情反转 | 钩子 | 钩子类型 | 情绪 | 节拍 |",
          "|---|---|---|---|---|---|---|---|"]
    L += ["| EP%02d | %s | %s | %s | %s | %s | %s | %s |"
          % (e["ep"], e["act"], e["core_conflict"], e["reversal"], e["hook"],
             e["hook_type"], e["emotion"], e["beat_note"]) for e in o["episodes"]]
    L += ["", "## 差异化红线（违反即失败）", ""] + ["- %s" % r for r in o["differentiation_redlines"]]
    return "\n".join(L)


def render_script_brief(o: dict) -> str:
    L = ["# ④ 改写指令书（%d 集规划已定稿）" % o["total_ep"], "",
         "> 本文件 + `blueprint.json` 是 LLM 写剧本的**唯一指令来源**；",
         "> 按 `stages/creation/prompts/s3-script-rewrite.md` 逐集产出 FFS 正文。", "",
         "## 一、项目", "",
         "- 剧名：%s（%s）　市场：%s　平台：%s　形态：%s"
         % (o["title"], o.get("title_es") or "-", o["market"], o["platform"], o["form"]),
         "- 集数：%d 集 × %d 秒　STYLE LOCK 须逐字复制：`%s`"
         % (o["total_ep"], o["ep_seconds"], o["style_lock"]),
         "- logline：%s" % (o["positioning_digest"].get("logline")),
         "- 核心冲突：%s" % (o["positioning_digest"].get("conflict")), "",
         "## 二、受众与差异化（来自③定位卡）", "",
         "- 受众画像：%s" % (o["positioning_digest"].get("audience_profile") or {}),
         "- 题材方向：%s" % (o["positioning_digest"].get("genre_direction")),
         "- 差异化卖点：%s" % "；".join(o["positioning_digest"].get("differentiation_selling_points") or []) or "-",
         "- ② 高频套路（可顺势）：%s" % ("、".join(o["hot_tropes"]) or "-"), "",
         "## 三、改写方式（源剧本 → 新规划）", "",
         "1. **保留**源剧本的核心人设、设定魅力与最「颠」的爽点机制。",
         "2. **重排**为 %d 集结构（见 blueprint.json 的 episodes），每集落实其" % o["total_ep"],
         "   「核心冲突 + 一次反转 + 集尾钩子」三件套。",
         "3. **加密**钩子与爆点：按 `boom_cadence` 植入中型/大型爆点。", "",
         "## 四、差异化红线（违反即失败）", ""] + ["- %s" % r for r in o["differentiation_redlines"]] + [
         "", "## 五、交付与自检", "",
         "1. 逐集输出 FFS 正文到 `<工作区>/script/ep_XX.md`（HEADER 六字段 + 镜头块 8 字段 + 可选 DUR）。",
         "2. 台词为口语西语；同时给中文译稿（bilingual）。",
         "3. 自检：`python script_rewrite.py lint --ep N` → 全 [OK] 后跑 `tools/cli.py check --ep N`。",
         ""]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# FFS 骨架
# ---------------------------------------------------------------------------
def build(ws: pathlib.Path, ep: int, shots: int = 8, emit: bool = False, dry: bool = False) -> int:
    bp = C.read_json(C.pdir(ws, C.BLUEPRINT))
    if not isinstance(bp, dict) or not bp.get("episodes"):
        print("[FAIL] 找不到④集数规划：%s（先跑 script_rewrite.py plan）" % C.pdir(ws, C.BLUEPRINT))
        return 2
    row = next((e for e in bp["episodes"] if e.get("ep") == ep), None)
    if not row:
        print("[FAIL] 规划里没有 EP%d（共 %d 集）" % (ep, bp.get("total_ep", 0)))
        return 2
    b = {
        "title": bp.get("title"), "total_ep": bp.get("total_ep"), "platform": bp.get("platform"),
        "style_lock": bp.get("style_lock"), "_ep_row": row,
    }
    skeleton = render_skeleton(ws, b, ep, shots)
    print(C.hr())
    print("④ 剧本改写 · FFS 骨架（EP%02d）" % ep)
    print(C.hr())
    print("  本集核心冲突：%s" % row["core_conflict"])
    print("  本集反转    ：%s" % row["reversal"])
    print("  本集钩子    ：[%s] %s" % (row["hook_type"], row["hook"]))
    if dry:
        print("[dry] 未落盘。\n")
        print(skeleton)
        return 0
    dst = C.pdir(ws, "creation/03-script/skeleton_ep_%02d.md" % ep)
    C.write_text(dst, skeleton)
    print("  [OK] 骨架：%s" % dst)
    if emit:
        target = C.pdir(ws, "script/ep_%02d.md" % ep)
        C.write_text(target, skeleton)
        print("  [OK] 已就位：%s（用 prompts/s3 填正文字段）" % target)
    print("  下一步：script_rewrite.py lint --ep %d" % ep)
    return 0


def render_skeleton(ws: pathlib.Path, b: dict, ep: int, shots: int) -> str:
    row = b.get("_ep_row") or {}
    # 角色/场景骨架：优先读 bible（若有），否则用占位
    bible = C.pdir(ws, "bible")
    char_line, scene_line = "<CHAR 代号>: <锚点>", "<SCENE 代号>: <描述>"
    chars_md = C.read_text(bible / "characters.md")
    scenes_md = C.read_text(bible / "scenes.md")
    if chars_md:
        codes = re.findall(r"\|\s*`([A-Z_]{2,})`", chars_md)
        if codes:
            char_line = "; ".join("%s: <锚点>" % c for c in codes[:6])
    if scenes_md:
        codes = re.findall(r"\|\s*`([A-Z_]{2,})`", scenes_md)
        if codes:
            scene_line = "; ".join("%s: <描述>" % c for c in codes[:6])

    L = ["# TITLE: %s" % b.get("title"),
         "# EP: %02d/%d" % (ep, b.get("total_ep", 0)),
         "# PLATFORM: %s" % b.get("platform"),
         "# STYLE LOCK: %s" % b.get("style_lock"),
         "# CHAR: %s" % char_line,
         "# SCENE: %s" % scene_line,
         ""]
    for i in range(1, shots + 1):
        L += ["---",
              "[SC-%02d]" % i,
              "SHOT: <景别, 运镜>",
              "LOCATION: <SCENE 代号>",
              "TIME: <日/夜/黄昏/黎明/雨夜>",
              "CHAR: <CHAR 代号 或 无>",
              "ACTION: <1-2句具体动作+表情；必须出现角色代号或名字；禁止抽象词>",
              "DIALOG: <\"西语台词\" 或 无；单句≤20字>",
              "VO: <NARRATOR: 旁白 或 无>",
              "SFX: <音效 或 无>",
              "DUR: %d" % (5 if i in (1, shots) else 8),
              ""]
    L += ["<!-- 本集核心冲突：%s -->" % row.get("core_conflict", ""),
          "<!-- 本集反转：%s -->" % row.get("reversal", ""),
          "<!-- 本集钩子（写进最后一个镜头）：[%s] %s -->" % (row.get("hook_type", ""), row.get("hook", "")),
          "<!-- 填写口径见 creation/03-script/script-brief.md 与 prompts/s3-script-rewrite.md -->"]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# FFS 结构自检
# ---------------------------------------------------------------------------
def lint(ws: pathlib.Path, ep: int) -> int:
    p = C.pdir(ws, "script/ep_%02d.md" % ep)
    if not p.is_file():
        print("[FAIL] 找不到剧本：%s" % p)
        return 2
    text = p.read_text(encoding="utf-8", errors="replace")
    errors: list[str] = []
    warns: list[str] = []

    for f in FFS_HEADER_ORDER:
        if not re.search(r"(?m)^#\s*%s\s*:" % re.escape(f), text):
            errors.append("缺 HEADER 字段 `# %s:`" % f)
    sl = re.search(r"(?m)^#\s*STYLE LOCK\s*:\s*(.+)$", text)
    if sl and not sl.group(1).strip():
        errors.append("STYLE LOCK 为空")

    blocks = re.split(r"(?m)^---\s*$", text)
    shot_blocks = [b for b in blocks if re.search(r"\[SC-\d+\]", b)]
    if not shot_blocks:
        errors.append("没有任何镜头块（应形如 `---` + `[SC-01]`）")
    for b in shot_blocks:
        m = re.search(r"\[SC-\d+\]", b)
        sid = m.group(0) if m else "?"
        for f in FFS_SHOT_FIELDS:
            if not re.search(r"(?m)^\s*%s\s*:" % f, b):
                errors.append("%s 缺字段 `%s`" % (sid, f))
        for bad in FFS_FORBIDDEN:
            if re.search(r"(?m)^\s*%s\s*:" % bad, b):
                errors.append("%s 出现禁止字段 `%s`（E004）" % (sid, bad))
        dur = re.search(r"(?m)^\s*DUR\s*:\s*(\d+)", b)
        if dur:
            d = int(dur.group(1))
            if not (DUR_MIN <= d <= DUR_MAX):
                errors.append("%s DUR=%d 越界（%d-%d）" % (sid, d, DUR_MIN, DUR_MAX))
        dl = re.search(r"(?m)^\s*DIALOG\s*:\s*(.+)$", b)
        if dl and dl.group(1).strip() not in ("无", ""):
            for sent in re.findall(r"[“\"']([^”\"']+)[”\"']", dl.group(1)):
                if len(sent.strip()) > 40:
                    warns.append("%s 台词偏长（%d 字）：%s" % (sid, len(sent.strip()), sent[:24]))

    n = len(shot_blocks)
    if not (SHOT_MIN <= n <= SHOT_MAX):
        errors.append("镜头数 %d 越界（口径 %d-%d）" % (n, SHOT_MIN, SHOT_MAX))

    durs = [int(m.group(1)) for m in re.finditer(r"(?m)^\s*DUR\s*:\s*(\d+)", text)]
    total = sum(durs) if durs else n * 8
    if not (TOTAL_MIN <= total <= TOTAL_MAX):
        warns.append("总时长约 %d 秒，超出口径 %d-%d 秒（缺 DUR 时按 8 秒/镜估算）"
                     % (total, TOTAL_MIN, TOTAL_MAX))

    # ★ 留存率结构校验（2026-10-16）：对册集规划里本集「四件套」做硬校验
    bp = C.read_json(C.pdir(ws, C.BLUEPRINT)) or {}
    row = next((e for e in (bp.get("episodes") or []) if e.get("ep") == ep), None)
    if isinstance(row, dict):
        for key, cn in (("cold_open", "开场钩子"), ("core_conflict", "核心冲突"),
                        ("reversal", "一次反转"), ("hook", "集尾钩子")):
            if not str(row.get(key) or "").strip():
                errors.append("留存结构缺 `%s`（%s）—— 见 blueprint.json 的 retention_plan" % (key, cn))
        # 钩子类型不得与前 2 集连续重复（防观众疲劳）
        eps = bp.get("episodes") or []
        cur = row.get("hook_type")
        prev2 = [e.get("hook_type") for e in eps if ep - 2 <= e.get("ep", 0) <= ep - 1]
        if cur and len(prev2) == 2 and prev2[0] == prev2[1] == cur:
            warns.append("EP%d 钩子类型「%s」与前两集连续重复（建议换型）" % (ep, cur))
    else:
        warns.append("blueprint.json 里没有 EP%d 的规划行，无法校验留存结构" % ep)

    print(C.hr())
    print("④ FFS 结构自检 · EP%02d（%s）" % (ep, p))
    print(C.hr())
    print("  镜头块：%d ｜ 估算总时长：%d 秒" % (n, total))
    for e in errors:
        print("  [FAIL] %s" % e)
    for w in warns:
        print("  [WARN] %s" % w)
    print("-" * 74)
    if errors:
        print("[NG] 结构自检未通过（%d 项错误）。修好再跑 tools/cli.py check。" % len(errors))
        return 1
    print("[OK] 结构自检通过。下一步：python tools/cli.py check --ep %d" % ep)
    return 0


REF_TEMPLATE = {
    "source_hits": ["<对标剧名>"], "track": "<赛道>",
    "beat_sheet": [{"act": "起", "beat": "<...>", "ep_range": "1-9"}],
    "hook_chain": [{"ep": 1, "type": "<钩子类型>", "hook": "<...>"}],
    "structure_notes": "<从对标剧抽出的结构规律，不是剧情复述>",
}


def cmd_ref_template(a) -> int:
    ws = C.resolve_ws(a.workspace)
    p = C.write_json(C.pdir(ws, C.REFERENCE_STRUCTURE), REF_TEMPLATE)
    print("[OK] 已写出对标结构模板：%s（可被 plan 参考）" % p)
    return 0


def main() -> int:
    C.harden_stdio()
    ap = argparse.ArgumentParser(description="④ 剧本改写：集数规划 + FFS 骨架 + 结构自检")
    ap.add_argument("--workspace", default=None)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan", help="把定位卡落成 N 集规划（默认 60）")
    p.add_argument("--rank", type=int, default=1)
    p.add_argument("--episodes", type=int, default=None)
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=lambda a: plan(C.resolve_ws(a.workspace), a.rank, a.episodes, a.dry))
    p = sub.add_parser("build", help="生成某集 FFS 骨架")
    p.add_argument("--ep", type=int, required=True)
    p.add_argument("--shots", type=int, default=8)
    p.add_argument("--emit", action="store_true")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=lambda a: build(C.resolve_ws(a.workspace), a.ep, a.shots, a.emit, a.dry))
    p = sub.add_parser("lint", help="对已填写剧本做 FFS 结构自检")
    p.add_argument("--ep", type=int, required=True)
    p.set_defaults(fn=lambda a: lint(C.resolve_ws(a.workspace), a.ep))
    p = sub.add_parser("ref-template", help="写出对标结构模板")
    p.set_defaults(fn=cmd_ref_template)
    a = ap.parse_args()
    if not getattr(a, "fn", None):
        return plan(C.resolve_ws(a.workspace))
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
