#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""离线测试：画风三轨门禁（style_gate）+ 半解说三类校验（half_narrated_check）

零网络、零计费、零第三方依赖。
    python stages/production/scripts/tests/test_style_gate.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))

import style_gate as SG            # noqa: E402
import half_narrated_check as HC   # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))


def _ws(tmp):
    ws = pathlib.Path(tmp)
    (ws / "05-分镜").mkdir(parents=True, exist_ok=True)
    return ws


def _sb(over=None, assets_over=None):
    sb = {
        "ep": "ep01",
        "shots": [{
            "shot_id": "01", "duration": 8.0,
            "shot_camera_angle": "three_quarter",
            "first_frame_anchor_role": "character_main_card",
            "first_frame_angle_match": {"anchor_view": "front_3/4", "angle_match_confirmed": True},
            "reset_anchor": True,
            "narration_lines": [{"text": "那一刻，我终于看清了门口的人。", "start": 0.0, "end": 3.6}],
            "dialogue_lines": [{"speaker": "萧珩", "line": "你该行礼了。", "start": 4.1, "end": 6.0}],
        }],
        "assets": {
            "characters": [{"name": "林夏", "main_card": "a.png", "three_view": "b.png"}],
            "scenes": [{"name": "客厅", "main_card": "c.png", "aux_card": "d.png"}],
            "props": [],
        },
    }
    return sb


def main():
    tmp = tempfile.mkdtemp(prefix="solodrama_style_")
    try:
        ws = _ws(tmp)

        # --- 门禁：未确认 ---
        st = SG.gate_state(ws)
        check("未确认时 gate_state.confirmed=False", st["confirmed"] is False)
        ok, msg = SG.require(ws)
        check("未确认时 require 不放行", ok is False and "尚未确认画风" in msg, msg)

        # --- set 3d_manhua ---
        rc = SG._do_set(ws, "3d_manhua", None)
        check("set 3d_manhua 返回 0", rc == 0, "rc=%s" % rc)
        p = json.loads((ws / SG.PLAN_NAME).read_text(encoding="utf-8"))
        check("确认单写入且子模式取默认",
              p["style"]["choice"] == "3d_manhua" and p["style"]["sub_mode"] == "guoman-3d-render")
        check("3D 规格：三视图+辅卡必须", p["style"]["spec"]["assets"]["character_3view"] == 1
              and p["style"]["spec"]["assets"]["scene_aux"] == 1)
        check("3D 规格：单镜 6-10 秒", p["style"]["spec"]["shot_duration"]["min"] == 6.0
              and p["style"]["spec"]["shot_duration"]["max"] == 10.0)

        # --- 非法子模式被拒 ---
        rc = SG._do_set(ws, "3d_manhua", "nope")
        check("非法子模式返回 2", rc == 2, "rc=%s" % rc)

        # --- require 与 expect 不符则拦下 ---
        ok, msg = SG.require(ws, expect="2d_animation")
        check("画风不符时 require 不放行", ok is False and "已确认画风" in msg, msg)
        ok, msg = SG.require(ws, expect="3d_manhua")
        check("画风相符时放行", ok is True, msg)

        # --- 2D 规格差异 ---
        SG._do_set(ws, "2d_animation", None)
        s2 = SG.STYLES["2d_animation"]
        check("2D 不要三视图/辅卡", s2["assets"]["character_3view"] == 0 and s2["assets"]["scene_aux"] == 0)
        check("2D 交付止于旁白交接", s2["delivery_stop"] == "narration_handoff")
        check("真人强制三格分镜", SG.STYLES["live_action"]["storyboard_topology"] == "three_panel_per_clip")

        # --- 三类校验：合规样本应通过 ---
        SG._do_set(ws, "3d_manhua", None)
        spec = SG.STYLES["3d_manhua"]
        sb = _sb()
        e, w = HC.check_windows(sb, spec)
        check("合规样本 windows 无硬失败", not e, str(e))
        e, _ = HC.check_assets(sb, spec)
        check("合规样本 assets 无硬失败", not e, str(e))
        e, _ = HC.check_anchors(sb, spec)
        check("合规样本 anchors 无硬失败", not e, str(e))

        # --- 缺陷样本应被拦 ---
        bad = _sb()
        bad["shots"][0]["narration_lines"] = [
            {"text": "她一步一步走近，每一步都像踩在我的心跳上，我几乎能听见自己血液倒流的声音，那种被看穿的恐惧让我无法呼吸。",
             "start": 0.0, "end": 8.0}]
        e, _ = HC.check_windows(bad, spec)
        check("旁白超字数被拦", any("> 上限" in x for x in e), str(e[:2]))
        check("尾缓冲越界被拦", any("尾缓冲" in x for x in e), str(e[:3]))

        bad2 = _sb()
        bad2["shots"].append({
            "shot_id": "02", "duration": 8.0, "shot_camera_angle": "tracking",
            "first_frame_anchor_role": "character_main_card",
            "first_frame_angle_match": {"angle_match_confirmed": True}, "reset_anchor": True,
            "narration_lines": [], "dialogue_lines": [],
        })
        e, _ = HC.check_anchors(bad2, spec)
        check("侧跟拍用正面卡被拦", any("应使用锚点 character_3view_side" in x for x in e), str(e))

        bad3 = _sb()
        bad3["assets"]["characters"][0].pop("three_view")
        bad3["assets"]["scenes"][0].pop("aux_card")
        bad3["assets"]["props"] = [{"name": "a", "card": "1.png"}, {"name": "b", "card": "2.png"}]
        e, _ = HC.check_assets(bad3, spec)
        check("缺三视图被拦", any("three_view" in x for x in e))
        check("缺场景辅卡被拦", any("aux_card" in x for x in e))
        check("道具卡超上限被拦", any("道具卡" in x for x in e))

        # --- 2D 不要求三视图（同一缺陷样本在 2D 下应通过）---
        e2, _ = HC.check_assets(bad3, SG.STYLES["2d_animation"])
        check("2D 不因缺三视图报错", not any("three_view" in x for x in e2), str(e2))

        # --- 跨镜间隔 ---
        bad4 = _sb()
        bad4["shots"][0]["dialogue_lines"] = [{"speaker": "萧珩", "line": "x", "start": 4.1, "end": 8.0}]
        bad4["shots"].append({"shot_id": "02", "duration": 8.0, "reset_anchor": True,
                              "first_frame_anchor_role": "scene_main_card",
                              "first_frame_angle_match": {"angle_match_confirmed": True},
                              "narration_lines": [{"text": "你好。", "start": 0.0, "end": 1.0}],
                              "dialogue_lines": []})
        e, _ = HC.check_windows(bad4, spec)
        check("跨镜间隔不足被拦", any("跨镜语音间隔" in x for x in e), str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 62)
    print("style_gate + half_narrated_check 离线测试")
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
