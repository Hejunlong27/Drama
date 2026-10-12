#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""test_model_gate.py —— 模型选择门禁的离线测试（零网络、零计费、零第三方依赖）

覆盖：
  1. 未确认时 gate_state.confirmed=False，require() 拦截
  2. set 后 confirmed=True，require() 放行
  3. 通道不一致（确认 local 却跑 rh）被拦下
  4. reset 后回到未确认状态
  5. 确认单读写往返一致、history 记录正确
  6. 三种选项 id 与 MODEL_OPTIONS 定义一致
  7. 菜单/确认请求渲染非空且包含全部选项标签

用法：python test_model_gate.py       （全绿退出码 0；有失败退出码 1）
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
GATE_PATH = HERE.parent / "model_gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("model_gate", GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _mk_ws() -> pathlib.Path:
    ws = pathlib.Path(tempfile.mkdtemp(prefix="drama-gate-test-"))
    (ws / "video-pipeline" / "data").mkdir(parents=True, exist_ok=True)
    (ws / "video-pipeline" / "data" / "shots.json").write_text("[]", encoding="utf-8")
    return ws


RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def main() -> int:
    mg = _load_gate()
    ws = _mk_ws()
    try:
        # 1. 未确认
        st = mg.gate_state(ws, "image")
        check("未确认时 image 未确认", st["confirmed"] is False, json.dumps(st, ensure_ascii=False))
        ok, msg = mg.require(ws, "image", expected_engine="local")
        check("未确认时 require 拦截", ok is False, msg)

        # 2. set 后放行
        mg.cmd_set(_ns(workspace=str(ws), pairs=["image=local_comfyui"], detail=None))
        st = mg.gate_state(ws, "image")
        check("set 后 image 已确认", st["confirmed"] is True and st["choice"] == "local_comfyui",
              json.dumps(st, ensure_ascii=False))
        ok, msg = mg.require(ws, "image", expected_engine="local")
        check("确认 local 后 require(local) 放行", ok is True, msg)

        # 3. 通道不一致被拦
        ok, msg = mg.require(ws, "image", expected_engine="rh")
        check("确认 local 却跑 rh 被拦", ok is False, msg[:80])

        # 4. video 独立（未确认）
        ok, _ = mg.require(ws, "video", expected_engine="workflow")
        check("video 尚未确认被拦", ok is False)

        # 5. 两条一起 set + 读写往返
        mg.cmd_set(_ns(workspace=str(ws), pairs=["video=runninghub"], detail="workflow_id=demo"))
        plan = mg.load_plan(ws)
        check("确认单落盘且可读回", plan["models"]["video"]["choice"] == "runninghub")
        check("detail 已记录", (plan["models"]["video"].get("detail") or {}).get("workflow_id") == "demo",
              json.dumps(plan["models"]["video"].get("detail"), ensure_ascii=False))
        ok, _ = mg.require(ws, "video", expected_engine="workflow")
        check("确认 runninghub 后 require(workflow) 放行", ok is True)

        # 6. reset
        mg.cmd_reset(_ns(workspace=str(ws), kind="image"))
        st = mg.gate_state(ws, "image")
        check("reset 后回到未确认", st["confirmed"] is False)
        check("reset 不清除 video", mg.gate_state(ws, "video")["confirmed"] is True)
        check("history 记录了 image 的清除前后",
              any(h["kind"] == "image" and h["from"] == "local_comfyui" and h["to"] is None
                  for h in mg.load_plan(ws)["history"]))

        # 7. 非法选项被拒（cmd_set 返回退出码 2）
        rc = mg.cmd_set(_ns(workspace=str(ws), pairs=["image=nonsense"], detail=None))
        check("非法选项返回码 2", rc == 2, "rc=%s" % rc)

        # 8. 选项 id 与清单一致
        for kind in ("image", "video"):
            ids = [o["id"] for o in mg.MODEL_OPTIONS[kind]]
            check("%s 选项为三选一" % kind, ids == ["local_comfyui", "runninghub", "closed_source"],
                  str(ids))

        # 9. 渲染非空且含全部标签
        for kind in ("image", "video"):
            menu = mg.render_menu(kind)
            req = mg.render_confirm_request(kind, ws)
            check("%s 菜单含全部标签" % kind,
                  all(o["label"] in menu for o in mg.MODEL_OPTIONS[kind]))
            check("%s 确认请求含三选项" % kind, "三选一" in req and "models" in req)
    finally:
        shutil.rmtree(ws, ignore_errors=True)

    print("=" * 62)
    print("model_gate 离线测试")
    print("=" * 62)
    n_fail = 0
    for name, ok, detail in RESULTS:
        print("  %s %s%s" % ("[OK]  " if ok else "[FAIL]", name,
                             "" if ok else "  <- %s" % detail))
        n_fail += 0 if ok else 1
    print("-" * 62)
    print("共 %d 例，通过 %d，失败 %d" % (len(RESULTS), len(RESULTS) - n_fail, n_fail))
    return 1 if n_fail else 0


def _ns(**kw):
    class _O:
        def __init__(self, d):
            self.__dict__.update(d)
    return _O(kw)


if __name__ == "__main__":
    sys.exit(main())
