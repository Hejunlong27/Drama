#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""style_gate.py —— 画风三轨「任务开始前确认」门禁（前置条件，与通道门禁并列）

为什么有它（2026-10-16 用户要求）
--------------------------------
短剧生产的**画风**决定后面一切的规格：资产要几张、单镜多长、旁白字数上限多少、
H3 提示词带哪段风格块、要不要三格时间分镜、交付到哪一步。这些差异极大且**不可事后弥补**，
所以必须在任务开始前一次性锁死。

三轨来源 = MiniMax 官方半解说短剧技能族（`~/.hub/skills/half-narrated-*-short-drama`）：
  * `3d_manhua`    3D 漫剧半解说短剧      —— 资产最重（角色三视图 + 场景辅卡）
  * `2d_animation` 2D 动画半解说短剧      —— 交付止于「旁白交接」，不串成片
  * `live_action`  半解说真人短剧        —— 强制「1 视频片段 = 1 张三格时间分镜」

设计约定
--------
* **零第三方依赖**（纯标准库），与其余生产脚本一致。
* 确认单落**工作区** `<ws>/style-plan.json`，不进仓库。
* **只拦「首次」**：确认过就不再问；换画风需 `reset` 后重确认。
* **不一致就拦**：确认了 A 画风却按 B 画风的规格产出 → 拦下，防「确认一套、做另一套」。
* 中文 Windows 控制台纪律：正文不用 GBK 装不下的符号；UTF-8 无 BOM 写盘。

命令
----
    python style_gate.py status                     查看当前确认状态（只读）
    python style_gate.py list                       打印可选画风清单
    python style_gate.py set 3d_manhua [--sub-mode guoman-3d-render]
    python style_gate.py set 2d_animation --sub-mode guoman-webtoon
    python style_gate.py set live_action
    python style_gate.py reset                      清除确认（换画风前用）
    python style_gate.py require [--expect 3d_manhua]   门禁查询（未过则退出码 4）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import sys

GATE_VERSION = 1
PLAN_NAME = "style-plan.json"
EXIT_WAIT_CONFIRM = 4   # 与 drama.py / model_gate.py 一致：4 = 等待用户确认

# ---------------------------------------------------------------------------
# 画风三轨（唯一真源；改这里即改所有下游菜单、校验与默认规格）
# ---------------------------------------------------------------------------
STYLES = {
    "3d_manhua": {
        "label": "3D 漫剧半解说短剧",
        "source_skill": "half-narrated-3d-short-drama",
        "desc": "3D 渲染 + 漫剧 webtoon 分镜语言。旁白推进 + 对白爆点。资产最重（角色三视图 + 场景辅卡）。",
        "visual_style_lock": "3D-manhua-cel",
        "sub_modes": {
            "guoman-3d-render": "国漫 3D 渲染流（默认）",
            "pixar-disney": "皮克斯 Disney 流",
            "semi-painterly-3d": "半厚涂 3D 写实流",
        },
        "default_sub_mode": "guoman-3d-render",
        # 单镜时长（秒）
        "shot_duration": {"min": 6.0, "max": 10.0, "action_min": 4.0, "action_max": 6.0},
        # 旁白字数硬上限（中文）
        "narration_max_chars": {"normal": 28, "emotion": 32, "monologue": 36},
        # 时间窗
        "reserve": {"narration_pct_max": 0.35, "dialogue_pct_max": 0.55,
                    "gap_min_s": 0.30, "tail_min_s": 0.30},
        "anchor_reset_cadence": {"protagonist": 4, "supporting": 6, "scene": 5},
        "assets": {"character_main": 1, "character_3view": 1,
                   "scene_main": 1, "scene_aux": 1, "prop_card_max": 1},
        "first_frame_required": True,       # 必传 opening_frame_image，禁纯文字
        "angle_match_required": True,       # 首帧角度必须与镜头角度匹配
        "storyboard_topology": "per_shot",  # 不强制三格
        "delivery_stop": "assembled_video", # 出无旁白成片 + 独立旁白包
        "narrator_volume_default": 1.8,
    },
    "2d_animation": {
        "label": "2D 动画半解说短剧",
        "source_skill": "half-narrated-2d-short-drama",
        "desc": "2D 赛璐珞 / 国漫 webtoon。旁白锁定语音窗口、保留原声对白。**交付止于旁白交接，不串成片。**",
        "visual_style_lock": "2D-cel-webtoon",
        "sub_modes": {
            "guoman-webtoon": "国漫 webtoon 流（默认）",
            "cel-anime": "日漫赛璐珞流",
            "light-color-webtoon": "轻彩漫 webtoon 流",
        },
        "default_sub_mode": "guoman-webtoon",
        "shot_duration": {"min": 4.0, "max": 10.0, "action_min": 4.0, "action_max": 10.0},
        "narration_max_chars": {"normal": 32, "emotion": 36, "monologue": 40},
        "reserve": {"narration_pct_max": 0.40, "dialogue_pct_max": 0.60,
                    "gap_min_s": 0.20, "tail_min_s": 0.20},
        "anchor_reset_cadence": {"protagonist": 3, "supporting": 5, "scene": 4},
        "assets": {"character_main": 1, "character_3view": 0,
                   "scene_main": 1, "scene_aux": 0, "prop_card_max": 1},
        "first_frame_required": False,      # 首帧是可选连续性参考
        "angle_match_required": False,
        "storyboard_topology": "per_shot",
        "delivery_stop": "narration_handoff",   # 终点 = 旁白交接
        "narrator_volume_default": 1.15,        # 语速默认 1.15（见下 speed 字段）
        "narrator_speed_default": 1.15,
    },
    "live_action": {
        "label": "半解说真人短剧",
        "source_skill": "half-narrated-live-action-short-drama",
        "desc": "真人写实。旁白推进 + 角色对白。**强制「1 视频片段 = 1 张三格时间分镜」**，交付成片 + 旁白 + 可选字幕。",
        "visual_style_lock": "live-action-photographic",
        "sub_modes": {},
        "default_sub_mode": None,
        "shot_duration": {"min": 5.0, "max": 15.0, "action_min": 5.0, "action_max": 15.0},
        "narration_max_chars": {"normal": 60, "emotion": 70, "monologue": 80},
        "reserve": {"narration_pct_max": 0.60, "dialogue_pct_max": 0.70,
                    "gap_min_s": 0.20, "tail_min_s": 0.20},
        "anchor_reset_cadence": {"protagonist": 0, "supporting": 0, "scene": 0},  # 不设固定周期
        "assets": {"character_main": 1, "character_3view": 0, "scene_main": 1,
                   "scene_aux": 0, "prop_card_max": 0},
        "first_frame_required": True,       # 干净单格图作构图/起始参考
        "angle_match_required": False,
        "storyboard_topology": "three_panel_per_clip",   # ★ 强制三格
        "delivery_stop": "final_video",
        "narrator_volume_default": 1.0,
    },
}

ORDER = ("3d_manhua", "2d_animation", "live_action")


# ---------------------------------------------------------------------------
def _harden_stdio() -> None:
    try:
        enc = "utf-8"
        if sys.stdout.isatty() and os.name == "nt":
            import ctypes
            cp = ctypes.windll.kernel32.GetConsoleOutputCP()
            if cp:
                enc = "cp%d" % cp
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(encoding=enc, errors="replace")   # type: ignore[union-attr]
            except Exception:                                     # noqa: BLE001
                pass
    except Exception:                                             # noqa: BLE001
        pass


def _resolve_ws(explicit=None) -> pathlib.Path:
    if explicit:
        p = pathlib.Path(explicit).expanduser()
        if p.is_dir():
            return p.resolve()
        sys.exit("[FAIL] --workspace 指向的目录不存在：%s" % p)
    env = (os.environ.get("SOLODRAMA_WORKSPACE") or os.environ.get("DRAMA_WORKSPACE"))
    if env:
        p = pathlib.Path(env).expanduser()
        if p.is_dir():
            return p.resolve()
        sys.exit("[FAIL] 环境变量 SOLODRAMA_WORKSPACE 指向的目录不存在：%s" % env)
    sys.exit("[FAIL] 找不到工作区。请用 --workspace 或环境变量 SOLODRAMA_WORKSPACE 指定。")


def plan_path(ws: pathlib.Path) -> pathlib.Path:
    return ws / PLAN_NAME


def _now() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def load_plan(ws: pathlib.Path) -> dict:
    p = plan_path(ws)
    if not p.is_file():
        return {"gate_version": GATE_VERSION, "style": None, "history": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {"gate_version": GATE_VERSION, "style": None, "history": []}
    d.setdefault("history", [])
    d.setdefault("gate_version", GATE_VERSION)
    return d


def save_plan(ws: pathlib.Path, plan: dict) -> pathlib.Path:
    p = plan_path(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
def gate_state(ws: pathlib.Path) -> dict:
    plan = load_plan(ws)
    st = plan.get("style") or {}
    if st.get("choice") and st.get("confirmed"):
        return {"confirmed": True, "choice": st["choice"],
                "sub_mode": st.get("sub_mode"), "reason": ""}
    return {"confirmed": False, "choice": None, "sub_mode": None,
            "reason": "本项目尚未确认画风（任务开始前必须确认）"}


def require(ws: pathlib.Path, expect: str | None = None) -> tuple:
    """门禁判定。返回 (是否放行, 提示语)。"""
    st = gate_state(ws)
    if not st["confirmed"]:
        return False, st["reason"]
    if expect and st["choice"] != expect:
        return False, ("本项目已确认画风为「%s」，但本次按「%s」执行。\n"
                       "        如确需换画风，请先 `drama.py style reset` 再重确认 —— 画风决定资产/时长/提示词规格，"
                       "中途更换会导致已产出素材全部作废。"
                       % (STYLES[st["choice"]]["label"], STYLES.get(expect, {}).get("label", expect)))
    s = STYLES[st["choice"]]
    sub = ("，子模式 %s" % st["sub_mode"]) if st.get("sub_mode") else ""
    return True, "已确认画风：%s（%s%s）" % (s["label"], st["choice"], sub)


def render_menu(indent: str = "    ") -> str:
    lines = []
    for i, k in enumerate(ORDER, 1):
        s = STYLES[k]
        lines.append("%s%d) %-24s %s" % (indent, i, s["label"], k))
        lines.append("%s    %s" % (indent, s["desc"]))
        lines.append("%s    规格：单镜 %g-%g 秒｜旁白上限 %d/%d/%d 字｜资产 角色主卡×1%s｜场景主卡×1%s｜交付到 %s"
                     % (indent, s["shot_duration"]["min"], s["shot_duration"]["max"],
                        s["narration_max_chars"]["normal"], s["narration_max_chars"]["emotion"],
                        s["narration_max_chars"]["monologue"],
                        "+三视图×1" if s["assets"]["character_3view"] else "",
                        "+辅卡×1" if s["assets"]["scene_aux"] else "",
                        s["delivery_stop"]))
        if s["sub_modes"]:
            lines.append("%s    子模式：%s" % (indent, " / ".join(
                "%s=%s" % (v, kk) for kk, v in s["sub_modes"].items())))
    return "\n".join(lines)


def render_request(ws: pathlib.Path) -> str:
    return ("=" * 74 + "\n"
            "【前置条件】任务开始前必须确认画风（本项目：%s）\n" % ws +
            "=" * 74 + "\n"
            "画风决定资产规格 / 单镜时长 / 旁白字数上限 / H3 提示词模板 / 交付终点，\n"
            "**不可事后更换**（换了等于已产出素材全部作废）。请三选一：\n\n" +
            render_menu() + "\n\n"
            "确认方式（任选其一）：\n"
            "  drama.py style --ask\n"
            "  drama.py style set 3d_manhua --sub-mode guoman-3d-render\n"
            "  drama.py style set 2d_animation --sub-mode guoman-webtoon\n"
            "  drama.py style set live_action\n")


# ---------------------------------------------------------------------------
def cmd_list(a) -> int:
    print("【画风三轨】可选：")
    print(render_menu())
    return 0


def cmd_status(a) -> int:
    ws = _resolve_ws(getattr(a, "workspace", None))
    plan = load_plan(ws)
    st = gate_state(ws)
    print("=" * 74)
    print("画风确认单（前置条件门禁）")
    print("=" * 74)
    print("工作区    : %s" % ws)
    print("确认单    : %s%s" % (plan_path(ws), "" if plan_path(ws).is_file() else "（尚未创建）"))
    if st["confirmed"]:
        s = STYLES[st["choice"]]
        print("画风      : [已确认] %s（%s）" % (s["label"], st["choice"]))
        if st.get("sub_mode"):
            print("子模式    : %s（%s）" % (st["sub_mode"], s["sub_modes"].get(st["sub_mode"], "?")))
        print("visual_style_lock : %s" % s["visual_style_lock"])
        print("单镜时长  : %g-%g 秒（动作/快切 %g-%g）"
              % (s["shot_duration"]["min"], s["shot_duration"]["max"],
                 s["shot_duration"]["action_min"], s["shot_duration"]["action_max"]))
        print("旁白上限  : %d / %d / %d 字（普通/情绪/大段）"
              % (s["narration_max_chars"]["normal"], s["narration_max_chars"]["emotion"],
                 s["narration_max_chars"]["monologue"]))
        print("交付终点  : %s" % s["delivery_stop"])
        print("确认时间  : %s" % ((plan.get("style") or {}).get("confirmed_at") or "-"))
    else:
        print("画风      : [未确认] 任务开始前必须确认")
    if getattr(a, "json", False):
        print()
        print(json.dumps({"workspace": str(ws), "plan": plan}, ensure_ascii=False, indent=2))
    return 0


def _do_set(ws: pathlib.Path, choice: str, sub_mode: str | None, source: str = "cli") -> int:
    if choice not in STYLES:
        print("[FAIL] 未知画风：%s（应为 %s）" % (choice, "|".join(ORDER)))
        return 2
    s = STYLES[choice]
    subs = s["sub_modes"]
    if subs:
        sub_mode = sub_mode or s["default_sub_mode"]
        if sub_mode not in subs:
            print("[FAIL] 未知子模式：%s（%s 可选 %s）" % (sub_mode, choice, "|".join(subs)))
            return 2
    else:
        if sub_mode:
            print("[WARN] %s 无子模式，忽略 --sub-mode %s" % (choice, sub_mode))
        sub_mode = None

    plan = load_plan(ws)
    prev = plan.get("style") or {}
    plan["style"] = {
        "choice": choice,
        "label": s["label"],
        "sub_mode": sub_mode,
        "visual_style_lock": s["visual_style_lock"],
        "source_skill": s["source_skill"],
        "confirmed": True,
        "confirmed_at": _now(),
        "confirmed_by": source,
        "spec": {k: s[k] for k in ("shot_duration", "narration_max_chars", "reserve",
                                   "anchor_reset_cadence", "assets", "first_frame_required",
                                   "angle_match_required", "storyboard_topology", "delivery_stop")},
    }
    if prev.get("choice"):
        plan.setdefault("history", []).append({"at": _now(), "from": prev.get("choice"), "to": choice})
    p = save_plan(ws, plan)
    print("[OK] 已确认画风 = %s（%s%s）" % (s["label"], choice,
                                            "，子模式 %s" % sub_mode if sub_mode else ""))
    print("     确认单：%s" % p)
    print("     规格已锁：单镜 %g-%g 秒｜旁白上限 %d/%d/%d 字｜资产 角色%s｜场景%s｜交付到 %s"
          % (s["shot_duration"]["min"], s["shot_duration"]["max"],
             s["narration_max_chars"]["normal"], s["narration_max_chars"]["emotion"],
             s["narration_max_chars"]["monologue"],
             "主卡+三视图" if s["assets"]["character_3view"] else "主卡",
             "主卡+辅卡" if s["assets"]["scene_aux"] else "主卡",
             s["delivery_stop"]))
    if s["storyboard_topology"] == "three_panel_per_clip":
        print("     [强制] 分镜拓扑 = 1 视频片段 → 1 张三格时间分镜图")
    return 0


def cmd_set(a) -> int:
    ws = _resolve_ws(getattr(a, "workspace", None))
    return _do_set(ws, a.choice, getattr(a, "sub_mode", None))


def cmd_reset(a) -> int:
    ws = _resolve_ws(getattr(a, "workspace", None))
    plan = load_plan(ws)
    prev = plan.pop("style", None)
    if prev:
        plan.setdefault("history", []).append({"at": _now(), "from": prev.get("choice"), "to": None})
        save_plan(ws, plan)
        print("[OK] 已清除画风确认（原为 %s）；下次执行会重新要求确认" % prev.get("choice"))
    else:
        print("[OK] 画风本就未确认，无需清除")
    return 0


def cmd_ask(a) -> int:
    ws = _resolve_ws(getattr(a, "workspace", None))
    if not sys.stdin.isatty():
        print(render_request(ws))
        print("[提示] 当前非交互式终端（无 TTY），请用 `drama.py style set <choice>` 记录确认。")
        return EXIT_WAIT_CONFIRM
    print("=" * 74)
    print("【前置条件】确认本项目画风（工作区：%s）" % ws)
    print("=" * 74)
    print(render_menu())
    while True:
        raw = input("请输入序号 1-%d（q 退出）：" % len(ORDER)).strip()
        if raw.lower() in ("q", "quit", "exit"):
            print("[取消] 未记录任何确认。")
            return EXIT_WAIT_CONFIRM
        if raw.isdigit() and 1 <= int(raw) <= len(ORDER):
            choice = ORDER[int(raw) - 1]
            sub = None
            subs = STYLES[choice]["sub_modes"]
            if subs:
                keys = list(subs)
                print("  子模式：")
                for i, k in enumerate(keys, 1):
                    print("    %d) %s（%s）" % (i, subs[k], k))
                while True:
                    r2 = input("  请输入子模式序号 1-%d（回车取默认 %s）："
                               % (len(keys), STYLES[choice]["default_sub_mode"])).strip()
                    if not r2:
                        sub = STYLES[choice]["default_sub_mode"]
                        break
                    if r2.isdigit() and 1 <= int(r2) <= len(keys):
                        sub = keys[int(r2) - 1]
                        break
                    print("  输入无效，请重试。")
            ok = input("确认选用「%s」？（y/N）：" % STYLES[choice]["label"]).strip().lower()
            if ok in ("y", "yes"):
                return _do_set(ws, choice, sub, source="interactive")
            print("已取消本次选择，请重新输入。")
            continue
        print("输入无效，请重试。")


def cmd_require(a) -> int:
    ws = _resolve_ws(getattr(a, "workspace", None))
    ok, msg = require(ws, expect=getattr(a, "expect", None))
    if ok:
        print("[OK] %s" % msg)
        return 0
    print(render_request(ws))
    print("[BLOCK] %s" % msg)
    return EXIT_WAIT_CONFIRM


def main() -> int:
    _harden_stdio()
    ap = argparse.ArgumentParser(description="画风三轨「任务开始前确认」门禁")
    ap.add_argument("--workspace", default=None, help="工作区（默认 SOLODRAMA_WORKSPACE）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("list", help="打印可选画风清单")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("status", help="查看当前确认状态（只读）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("set", help="记录确认：set 3d_manhua|2d_animation|live_action [--sub-mode ...]")
    p.add_argument("choice", choices=list(ORDER))
    p.add_argument("--sub-mode", dest="sub_mode", default=None)
    p.set_defaults(fn=cmd_set)

    p = sub.add_parser("reset", help="清除确认（换画风前用）")
    p.set_defaults(fn=cmd_reset)

    p = sub.add_parser("ask", help="交互式引导确认（需 TTY）")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("require", help="门禁查询（未过则退出码 4；供 drama.py 调用）")
    p.add_argument("--expect", default=None, choices=list(ORDER),
                   help="本次实际要用的画风；与确认不符则拦下")
    p.set_defaults(fn=cmd_require)

    a = ap.parse_args()
    if not getattr(a, "fn", None):
        return cmd_status(a)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
