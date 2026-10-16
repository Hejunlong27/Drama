#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""half_narrated_check.py —— 半解说短剧的三类硬校验（旁白时间窗 / 资产规格 / 首帧锚点）

为什么有它
----------
`half-narrated-*-short-drama` 三个官方技能把「半解说」的结构要求写成了硬关卡，但**靠人查**。
本脚本把其中**可确定性判定**的部分固化为项目内预置校验，按 `style_gate.py` 确认的画风分支取规格：

  windows  旁白/对白时间窗：字数上限、不重叠间隔、占比上限、尾缓冲、跨镜间隔
  assets   资产规格与数量：角色主卡/三视图、场景主卡/辅卡、道具卡上限（3D 要三视图+辅卡，2D 不要）
  anchors  首帧锚点（3D 硬关卡）：锚点必填、角度必须匹配、重置锚周期

输入契约：`<05-分镜>/storyboard.json`（schema 见 `docs/styles/画风三轨规范.md`）

用法
----
    python half_narrated_check.py --workspace "D:/剧/新剧" windows
    python half_narrated_check.py --workspace "D:/剧/新剧" all
退出码：0 通过｜2 有硬失败｜4 画风未确认
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import style_gate as SG  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff]|[A-Za-z]+")

# 3D 特有：镜头角度 → 必须使用的锚点视角
ANGLE_REQUIRED_ANCHOR = {
    "side": "character_3view_side",
    "tracking": "character_3view_side",
    "back": "character_3view_back",
    "front": "character_main_card",
    "three_quarter": "character_main_card",
    "wide": "scene_main_card",
    "overhead": "scene_aux_card",
    "close_up": "prop_card",
}


def nchars(text: str) -> int:
    """旁白字数：汉字按 1 字，英文按 1 词 1 字，标点空白不计。"""
    return len(CJK.findall(text or ""))


def load_storyboard(ws: pathlib.Path) -> tuple:
    """返回 (storyboard dict, 路径)。优先 05-分镜/storyboard.json，回退旧布局。"""
    cands = [ws / "05-分镜" / "storyboard.json", ws / "storyboard" / "storyboard.json"]
    try:
        lay = json.loads((ws / "layout.json").read_text(encoding="utf-8-sig"))
        d = (lay.get("stages") or {}).get("storyboard")
        if d:
            cands.insert(0, ws / d / "storyboard.json")
    except Exception:                                                     # noqa: BLE001
        pass
    for p in cands:
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8-sig")), p
    return None, cands[0]


# ---------------------------------------------------------------------------
def check_windows(sb: dict, spec: dict) -> tuple:
    errs, warns = [], []
    shots = sb.get("shots") or []
    if not shots:
        return ["storyboard.json 里没有 shots[]"], warns
    sd = spec["shot_duration"]
    rr = spec["reserve"]
    nmax = spec["narration_max_chars"]

    # 跨镜语音区间（全局时间）—— 用于跨镜 gap 校验
    global_spans, t0 = [], 0.0
    for s in shots:
        sid = str(s.get("shot_id") or "?")
        dur = float(s.get("duration") or 0)
        if not dur:
            errs.append("%s: 缺 duration（无法校验时间窗）" % sid)
            t0 += 0.0
            continue
        lo, hi = (sd["action_min"], sd["action_max"]) if s.get("is_action") else (sd["min"], sd["max"])
        if not (lo - 1e-6 <= dur <= hi + 1e-6):
            errs.append("%s: 时长 %.1fs 越界（%s 口径 %g-%g 秒）"
                        % (sid, dur, "动作/快切" if s.get("is_action") else "普通", lo, hi))

        nar = s.get("narration_lines") or []
        dia = s.get("dialogue_lines") or []
        if not nar and not dia:
            warns.append("%s: 无旁白也无对白（纯视觉镜；半解说结构下请确认是否漏写）" % sid)

        # 旁白：字数上限 + 占比
        kind = "monologue" if s.get("is_monologue") else ("emotion" if s.get("is_emotion") else "normal")
        nar_span, dia_span = None, None
        for n in nar:
            c = nchars(n.get("text"))
            lim = nmax[kind]
            if c > lim:
                errs.append("%s: 旁白 %d 字 > 上限 %d 字（%s 镜）—— 分镜阶段就拆，禁止后期加速或删字"
                            % (sid, c, lim, kind))
            a, b = n.get("start"), n.get("end")
            if a is None or b is None:
                errs.append("%s: 旁白缺 start/end（半解说必须在分镜阶段预留时间窗）" % sid)
                continue
            if float(b) <= float(a):
                errs.append("%s: 旁白窗口非法 [%s, %s]" % (sid, a, b))
            nar_span = (float(a), float(b)) if nar_span is None else \
                (min(nar_span[0], float(a)), max(nar_span[1], float(b)))
        for d in dia:
            a, b = d.get("start"), d.get("end")
            if a is None or b is None:
                errs.append("%s: 对白缺 start/end" % sid)
                continue
            if not d.get("speaker"):
                errs.append("%s: 对白缺 speaker（口型归属必须唯一）" % sid)
            if float(b) <= float(a):
                errs.append("%s: 对白窗口非法 [%s, %s]" % (sid, a, b))
            dia_span = (float(a), float(b)) if dia_span is None else \
                (min(dia_span[0], float(a)), max(dia_span[1], float(b)))

        # 同镜不重叠 + 最小间隔
        if nar_span and dia_span:
            gap = (dia_span[0] - nar_span[1]) if nar_span[1] <= dia_span[0] else (nar_span[0] - dia_span[1])
            if gap < 0:
                errs.append("%s: 旁白与对白窗口重叠（narration %s / dialogue %s）—— 禁止同轨重叠"
                            % (sid, nar_span, dia_span))
            elif gap + 1e-6 < rr["gap_min_s"]:
                errs.append("%s: 旁白与对白间隔 %.2fs < 最小 %.2fs" % (sid, gap, rr["gap_min_s"]))

        # 占比上限 + 尾缓冲
        nar_pct = (nar_span[1] - nar_span[0]) / dur if nar_span else 0.0
        dia_pct = (dia_span[1] - dia_span[0]) / dur if dia_span else 0.0
        if nar_pct > rr["narration_pct_max"] + 1e-6:
            warns.append("%s: 旁白占镜头 %.0f%% > 建议上限 %.0f%%"
                         % (sid, nar_pct * 100, rr["narration_pct_max"] * 100))
        if dia_pct > rr["dialogue_pct_max"] + 1e-6:
            warns.append("%s: 对白占镜头 %.0f%% > 建议上限 %.0f%%"
                         % (sid, dia_pct * 100, rr["dialogue_pct_max"] * 100))
        last_end = max([x for x in (nar_span[1] if nar_span else None,
                                    dia_span[1] if dia_span else None) if x is not None] or [0.0])
        if last_end + rr["tail_min_s"] > dur + 1e-6:
            errs.append("%s: 语音结束 %.2fs + 尾缓冲 %.2fs > 镜头时长 %.1fs（放不下 → 延长镜头或拆句）"
                        % (sid, last_end, rr["tail_min_s"], dur))

        for span in (nar_span, dia_span):
            if span:
                if span[0] < -1e-6 or span[1] > dur + 1e-6:
                    errs.append("%s: 语音区间 %s 超出镜头范围 [0, %.1f]" % (sid, span, dur))
                global_spans.append((t0 + span[0], t0 + span[1]))

        t0 += dur

    # 跨镜最小间隔
    spans = sorted(global_spans)
    for i in range(1, len(spans)):
        g = spans[i][0] - spans[i - 1][1]
        if g < 0:
            errs.append("跨镜语音重叠：%s 与 %s" % (spans[i - 1], spans[i]))
        elif g + 1e-6 < rr["gap_min_s"]:
            errs.append("跨镜语音间隔 %.2fs < 最小 %.2fs（%s → %s）"
                        % (g, rr["gap_min_s"], spans[i - 1], spans[i]))
    return errs, warns


def check_assets(sb: dict, spec: dict) -> tuple:
    errs, warns = [], []
    need = spec["assets"]
    A = sb.get("assets") or {}
    chars = A.get("characters") or []
    scenes = A.get("scenes") or []
    props = A.get("props") or []
    if not chars:
        errs.append("assets.characters 为空（至少需要 1 个主要角色）")
    if not scenes:
        errs.append("assets.scenes 为空（至少需要 1 个主要场景）")
    for c in chars:
        nm = c.get("name") or "?"
        if not c.get("main_card"):
            errs.append("角色「%s」缺 main_card（角色主卡）" % nm)
        if need["character_3view"] and not c.get("three_view"):
            errs.append("角色「%s」缺 three_view（%s 是硬约束：3D 有真实旋转/轨道运镜，"
                        "缺三视图必然脸/服装漂移）" % (nm, spec_probe_label(spec)))
    for s in scenes:
        nm = s.get("name") or "?"
        if not s.get("main_card"):
            errs.append("场景「%s」缺 main_card（场景主卡）" % nm)
        if need["scene_aux"] and not s.get("aux_card"):
            errs.append("场景「%s」缺 aux_card（场景辅卡，3D 硬约束：不同运镜视角透视/遮挡差异大）" % nm)
    if len(props) > need["prop_card_max"]:
        errs.append("道具卡 %d 张 > 上限 %d 张" % (len(props), need["prop_card_max"]))
    if not props:
        warns.append("无道具卡（若某道具是多镜特写焦点，建议登记 1 张；否则把它画进角色主卡）")
    return errs, warns


def spec_probe_label(spec: dict) -> str:
    return "3D 漫剧" if spec.get("angle_match_required") else "本画风"


def check_anchors(sb: dict, spec: dict) -> tuple:
    errs, warns = [], []
    shots = sb.get("shots") or []
    cad = spec["anchor_reset_cadence"]
    hard = bool(spec.get("first_frame_required") and spec.get("angle_match_required"))

    for s in shots:
        sid = str(s.get("shot_id") or "?")
        role = s.get("first_frame_anchor_role")
        if not role:
            (errs if hard else warns).append(
                "%s: 缺 first_frame_anchor_role（首帧锚点来源必须显式声明，不能只写「参考这张图」）" % sid)
        if hard:
            am = s.get("first_frame_angle_match") or {}
            if not am:
                errs.append("%s: 缺 first_frame_angle_match（3D 硬关卡：角度不匹配必然漂移）" % sid)
            elif not am.get("angle_match_confirmed"):
                errs.append("%s: first_frame_angle_match.angle_match_confirmed ≠ true（不匹配不允许生成）" % sid)
            else:
                ang = (s.get("shot_camera_angle") or "").lower()
                need = ANGLE_REQUIRED_ANCHOR.get(ang)
                if need and role and role != need and not str(role).startswith(("scene_", "prop_", "last_frame")):
                    errs.append("%s: 镜头角度 %s 应使用锚点 %s，实际声明 %s（侧跟拍用侧三视、背面用背三视）"
                                % (sid, ang, need, role))

    # 重置锚周期：每连续 N 镜内至少一次 reset_anchor
    if cad and cad.get("protagonist"):
        n = int(cad["protagonist"])
        run = 0
        for s in shots:
            if s.get("reset_anchor"):
                run = 0
            else:
                run += 1
                if run >= n:
                    errs.append("%s: 连续 %d 镜未重置锚（主角每 %d 镜必须回主卡重置一次）"
                                % (s.get("shot_id"), run, n))
                    run = 0
    if not shots:
        warns.append("storyboard.shots 为空，锚点校验跳过")
    return errs, warns


# ---------------------------------------------------------------------------
def run(ws: pathlib.Path, which: str) -> int:
    st = SG.gate_state(ws)
    if not st["confirmed"]:
        SG.cmd_require(argparse.Namespace(workspace=str(ws), expect=None))
        return SG.EXIT_WAIT_CONFIRM
    spec = SG.STYLES[st["choice"]]
    sb, path = load_storyboard(ws)
    print("=" * 74)
    print("半解说结构校验（画风：%s / %s）" % (spec["label"], st["choice"]))
    print("=" * 74)
    if sb is None:
        print("[FAIL] 找不到分镜文件：%s" % path)
        print("       请先产出 storyboard.json（schema 见 docs/styles/画风三轨规范.md）")
        return 2
    print("  分镜文件: %s" % path)
    print("  镜头数  : %d" % len(sb.get("shots") or []))

    jobs = {"windows": check_windows, "assets": check_assets, "anchors": check_anchors}
    todo = list(jobs) if which == "all" else [which]
    all_errs, all_warns = [], []
    for name in todo:
        e, w = jobs[name](sb, spec)
        print("\n  ── %s ──" % name)
        for x in w:
            print("  [WARN] %s" % x)
        for x in e:
            print("  [FAIL] %s" % x)
        if not e and not w:
            print("  [OK] 通过")
        all_errs += e
        all_warns += w
    print("-" * 74)
    print("合计：%d 硬失败 ｜ %d 警告" % (len(all_errs), len(all_warns)))
    if all_errs:
        print("[BLOCK] 有硬失败 —— 不得进入生成（错误挡在计费之前）。")
        return 2
    print("[OK] 通过。")
    return 0


def main() -> int:
    SG._harden_stdio()
    ap = argparse.ArgumentParser(description="半解说结构三类硬校验（旁白时间窗 / 资产规格 / 首帧锚点）")
    ap.add_argument("--workspace", default=None)
    ap.add_argument("which", nargs="?", default="all",
                    choices=["all", "windows", "assets", "anchors"])
    a = ap.parse_args()
    ws = SG._resolve_ws(a.workspace)
    return run(ws, a.which)


if __name__ == "__main__":
    sys.exit(main())
