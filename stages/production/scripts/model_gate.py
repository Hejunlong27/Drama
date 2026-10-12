#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""model_gate.py —— 生图 / 视频模型的「首次执行前确认」门禁（项目级前置条件）

为什么有这个脚本
----------------
本项目此前默认「资产出图走本地 ComfyUI、出片走 RunningHub 工作流」，通道选择散落在
各命令的 `--engine` / `--channel` 参数里，**没有任何一处记录「这个项目到底打算用哪个模型」**。
后果：

* 换一部剧、换一个同事，谁都不知道上一部剧用的是哪条通道；
* 闭源模型（可灵/即梦/Veo/Sora/Midjourney 等）无法表达——它们既不是本地 ComfyUI，
  也不是 RunningHub，只能人工导出后落盘，但流程里没有位置声明它；
* 计费动作发生在「第一次生图 / 第一次出片」，而这两处此前只校验「有没有 Key」，
  不校验「用户是否已经决定用哪个模型」——等于替用户做了花钱的决定。

本脚本把「模型选择」变成一个**显式、留痕、可拦截**的前置条件：

    每个项目，在**首次生图**与**首次生成视频**之前，必须先向用户确认模型选择，
    选项为「本地 ComfyUI / RunningHub / 闭源模型」，确认结果写入 <工作区>/model-plan.json；
    `drama.py assets --submit` 与 `drama.py video run --yes` 会先查这份确认单，未确认即停。

设计约定
--------
* **零第三方依赖**（纯标准库），与引擎其余脚本一致。
* **不进仓库的数据**：确认单落在**工作区**（`<workspace>/model-plan.json`），不落在本仓库。
* **只拦「首次」**：一旦某类模型确认过，后续运行不再拦截（换模型需显式 `--reset` 后重确认）。
* **不一致就拦**：确认了本地 ComfyUI，却跑 `--engine rh` —— 拦下并要求先改确认单，
  防止「确认一套、跑另一套」的账实不符。
* **中文 Windows 控制台纪律**：正文不使用 GBK 装不下的符号；UTF-8 无 BOM 写盘。

命令（也可由 `drama.py models` 转发）
------------------------------------
    python model_gate.py status                     查看当前确认状态（只读）
    python model_gate.py list                       打印可选模型清单
    python model_gate.py ask                        交互式引导确认（需 TTY）
    python model_gate.py set image=local_comfyui    记录确认（非交互；可同时 set video=...）
    python model_gate.py set video=runninghub --detail "工作流 ID=xxx"
    python model_gate.py reset image                清除某类确认（换模型前用）
    python model_gate.py require image [--engine local]   门禁查询（供 drama.py 调用；未过则退出码 4）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import sys

# ---------------------------------------------------------------------------
# 可选模型清单（唯一真源；改这里即改所有下游菜单与校验）
# ---------------------------------------------------------------------------
GATE_VERSION = 1

MODEL_OPTIONS = {
    "image": [
        {
            "id": "local_comfyui",
            "label": "本地 ComfyUI（￥0）",
            "cost": "￥0（占本机 GPU/电费）",
            "desc": "本机 ComfyUI 出图（默认工作流 Qwen-Image 2.1）；数据不出局域网，零计费。",
            "engine_arg": "local",
        },
        {
            "id": "runninghub",
            "label": "RunningHub AI 应用（★计费）",
            "cost": "人物约 17-27 RH币/张；场景约 $0.01/张",
            "desc": "调用 RunningHub 上的 AI 应用出定妆板/场景空镜；需消费级 Key，云端计费。",
            "engine_arg": "rh",
        },
        {
            "id": "closed_source",
            "label": "闭源模型（★计费）",
            "cost": "按各家订阅/点数计费（站外）",
            "desc": "可灵/即梦/Midjourney/Nano Banana(Gemini) 等；本仓库不直连，"
                    "由人工在各平台出图后落盘到工作区，引擎按本地文件接管。",
            "engine_arg": None,
        },
    ],
    "video": [
        {
            "id": "local_comfyui",
            "label": "本地 ComfyUI（￥0）",
            "cost": "￥0（占本机 GPU/电费）",
            "desc": "本机 ComfyUI 跑视频工作流（如 Wan / Hunyuan 等本地视频模型）；零计费、数据不出网。",
            "engine_arg": None,
        },
        {
            "id": "runninghub",
            "label": "RunningHub 工作流（★计费）",
            "cost": "约 78-209 RH币/段（按帧数）",
            "desc": "RunningHub 标准通道工作流出片（九宫格→裁剪→H3 视频）；云端计费。",
            "engine_arg": "workflow",
        },
        {
            "id": "closed_source",
            "label": "闭源模型（★计费）",
            "cost": "按各家订阅/点数计费（站外）",
            "desc": "可灵/Veo/Sora/即梦/Runway 等；本仓库不直连，人工在各平台出片后落盘，引擎按本地文件接管。",
            "engine_arg": None,
        },
    ],
}

KINDS = {"image": "生图", "video": "视频"}
PLAN_NAME = "model-plan.json"
EXIT_WAIT_CONFIRM = 4   # 与 drama.py 退出码约定一致：4 = 等待用户确认（关卡停点）


# ---------------------------------------------------------------------------
# 控制台编码（与 drama.py/_harden_stdio 同口径）
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
                s.reconfigure(encoding=enc, errors="replace")  # type: ignore[union-attr]
            except Exception:                                   # noqa: BLE001
                pass
    except Exception:                                           # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 工作区与确认单读写
# ---------------------------------------------------------------------------
def _resolve_ws(explicit: str | None = None) -> pathlib.Path:
    """按 `--workspace > DRAMA_WORKSPACE > 脚本位置推断` 解析工作区（与 _ws.py 同口径，但不退出）。"""
    if explicit:
        p = pathlib.Path(explicit).expanduser()
        if p.is_dir():
            return p.resolve()
        sys.exit("[FAIL] --workspace 指向的目录不存在：%s" % p)
    env = os.environ.get("DRAMA_WORKSPACE")
    if env:
        p = pathlib.Path(env).expanduser()
        if p.is_dir():
            return p.resolve()
        sys.exit("[FAIL] 环境变量 DRAMA_WORKSPACE 指向的目录不存在：%s" % p)
    # 脚本位置推断：<ws>/scripts/model_gate.py （收编后脚本住在仓库里，故一般靠前两条）
    cand = pathlib.Path(__file__).resolve().parent.parent
    if (cand / "video-pipeline").is_dir() or (cand / "model-plan.json").is_file():
        return cand
    sys.exit("[FAIL] 找不到工作区。请用 --workspace 或环境变量 DRAMA_WORKSPACE 指定。")


def plan_path(ws: pathlib.Path) -> pathlib.Path:
    return ws / PLAN_NAME


def load_plan(ws: pathlib.Path) -> dict:
    p = plan_path(ws)
    if not p.is_file():
        return {"gate_version": GATE_VERSION, "models": {}, "history": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"gate_version": GATE_VERSION, "models": {}, "history": []}
    d.setdefault("models", {})
    d.setdefault("history", [])
    d.setdefault("gate_version", GATE_VERSION)
    return d


def save_plan(ws: pathlib.Path, plan: dict) -> pathlib.Path:
    p = plan_path(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def _now() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def _find_option(kind: str, choice: str) -> dict | None:
    for o in MODEL_OPTIONS[kind]:
        if o["id"] == choice:
            return o
    return None


# ---------------------------------------------------------------------------
# 门禁查询（给 drama.py 用）
# ---------------------------------------------------------------------------
def gate_state(ws: pathlib.Path, kind: str) -> dict:
    """返回某类模型的门禁状态：{'confirmed': bool, 'choice': str|None, 'reason': str}"""
    plan = load_plan(ws)
    m = (plan.get("models") or {}).get(kind) or {}
    if m.get("confirmed") and m.get("choice"):
        return {"confirmed": True, "choice": m["choice"], "reason": ""}
    return {"confirmed": False, "choice": None,
            "reason": "本项目尚未确认%s模型（首次执行前必须确认）" % KINDS[kind]}


def require(ws: pathlib.Path, kind: str, expected_engine: str | None = None) -> tuple[bool, str]:
    """门禁判定。返回 (是否放行, 提示语)。

    * 未确认 → 不放行（提示如何确认）；
    * 已确认但命令行通道与确认不符 → 不放行（防止「确认一套、跑另一套」）。
    """
    st = gate_state(ws, kind)
    if not st["confirmed"]:
        return False, st["reason"]
    choice = st["choice"]
    opt = _find_option(kind, choice) or {}
    # 通道一致性校验：仅当本次调用能确定「实际用的通道」时才比对
    if expected_engine is not None and opt.get("engine_arg") is not None:
        if expected_engine != opt["engine_arg"]:
            return False, (
                "本项目已确认的%s模型是「%s」（对应通道 %s），但本次执行用的是 %s。\n"
                "        如确需改用另一条通道，请先重新确认：drama models --set %s=<choice>"
                % (KINDS[kind], opt.get("label", choice), opt.get("engine_arg"),
                   expected_engine, kind)
            )
    return True, "已确认：%s（%s）" % (opt.get("label", choice), choice)


# ---------------------------------------------------------------------------
# 菜单渲染
# ---------------------------------------------------------------------------
def render_menu(kind: str, indent: str = "    ") -> str:
    lines = []
    for i, o in enumerate(MODEL_OPTIONS[kind], 1):
        lines.append("%s%d) %-26s %s" % (indent, i, o["label"], o["cost"]))
        lines.append("%s    %s" % (indent, o["desc"]))
    return "\n".join(lines)


def render_confirm_request(kind: str, ws: pathlib.Path) -> str:
    """未确认时打印给用户/agent 的确认请求（含可照抄的命令）。"""
    return (
        "=" * 74 + "\n"
        "【前置条件】首次%s前必须确认模型（本项目：%s）\n" % (KINDS[kind], ws) +
        "=" * 74 + "\n"
        "请选择本项目的%s模型（三选一）：\n" % KINDS[kind] +
        render_menu(kind) + "\n"
        "确认方式（任选其一）：\n"
        "  交互式：drama models --ask\n"
        "  命令行：drama models --set %s=<local_comfyui|runninghub|closed_source>\n" % kind +
        "确认后本项目的后续执行不再询问；换模型请用 drama models --reset %s 后重确认。\n" % kind
    )


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------
def cmd_list(a) -> int:
    for kind in ("image", "video"):
        print("【%s模型】可选：" % KINDS[kind])
        print(render_menu(kind))
        print()
    return 0


def cmd_status(a) -> int:
    ws = _resolve_ws(getattr(a, "workspace", None))
    plan = load_plan(ws)
    print("=" * 74)
    print("模型选择确认单（前置条件门禁）")
    print("=" * 74)
    print("工作区    : %s" % ws)
    print("确认单    : %s%s" % (plan_path(ws), "" if plan_path(ws).is_file() else "（尚未创建）"))
    for kind in ("image", "video"):
        st = gate_state(ws, kind)
        m = (plan.get("models") or {}).get(kind) or {}
        if st["confirmed"]:
            opt = _find_option(kind, st["choice"]) or {}
            print("%-9s : [已确认] %s（%s）  于 %s"
                  % (KINDS[kind], opt.get("label", st["choice"]), st["choice"], m.get("confirmed_at", "-")))
            if m.get("detail"):
                print("            detail: %s" % json.dumps(m["detail"], ensure_ascii=False))
        else:
            print("%-9s : [未确认] 首次%s前需确认" % (KINDS[kind], KINDS[kind]))
    if getattr(a, "json", False):
        print()
        print(json.dumps({"workspace": str(ws), "plan": plan}, ensure_ascii=False, indent=2))
    return 0


def _do_set(ws: pathlib.Path, kind: str, choice: str, detail: dict | None,
            source: str = "cli") -> int:
    if kind not in KINDS:
        print("[FAIL] 未知分类：%s（应为 image / video）" % kind)
        return 2
    opt = _find_option(kind, choice)
    if not opt:
        valid = "|".join(o["id"] for o in MODEL_OPTIONS[kind])
        print("[FAIL] 未知选项：%s（应为 %s）" % (choice, valid))
        return 2
    plan = load_plan(ws)
    prev = (plan.get("models") or {}).get(kind)
    plan.setdefault("models", {})[kind] = {
        "choice": choice,
        "label": opt["label"],
        "cost": opt["cost"],
        "engine_arg": opt["engine_arg"],
        "confirmed": True,
        "confirmed_at": _now(),
        "confirmed_by": source,
        "detail": detail or {},
    }
    if prev:
        plan.setdefault("history", []).append({"at": _now(), "kind": kind,
                                               "from": prev.get("choice"), "to": choice})
    p = save_plan(ws, plan)
    print("[OK] 已确认 %s模型 = %s（%s）" % (KINDS[kind], opt["label"], choice))
    print("     确认单：%s" % p)
    return 0


def cmd_set(a) -> int:
    ws = _resolve_ws(getattr(a, "workspace", None))
    detail = {}
    if a.detail:
        # 允许 --detail "k=v,k2=v2" 或整段 JSON
        s = a.detail.strip()
        if s.startswith("{"):
            try:
                detail = json.loads(s)
            except json.JSONDecodeError:
                print("[FAIL] --detail 不是合法 JSON")
                return 2
        else:
            for kv in s.split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    detail[k.strip()] = v.strip()
    rc = 0
    for item in a.pairs:
        if "=" not in item:
            print("[FAIL] 参数格式应为 kind=choice：%s" % item)
            return 2
        kind, choice = item.split("=", 1)
        rc |= _do_set(ws, kind.strip(), choice.strip(), detail)
    return rc


def cmd_reset(a) -> int:
    ws = _resolve_ws(getattr(a, "workspace", None))
    kind = a.kind
    if kind not in KINDS:
        print("[FAIL] 未知分类：%s（应为 image / video）" % kind)
        return 2
    plan = load_plan(ws)
    prev = (plan.get("models") or {}).pop(kind, None)
    if prev:
        plan.setdefault("history", []).append({"at": _now(), "kind": kind,
                                               "from": prev.get("choice"), "to": None})
        save_plan(ws, plan)
        print("[OK] 已清除 %s模型确认（原为 %s）；下次执行会重新要求确认"
              % (KINDS[kind], prev.get("choice")))
    else:
        print("[OK] %s模型本就未确认，无需清除" % KINDS[kind])
    return 0


def cmd_ask(a) -> int:
    ws = _resolve_ws(getattr(a, "workspace", None))
    if not sys.stdin.isatty():
        print(render_confirm_request(a.kind, ws))
        print("[提示] 当前非交互式终端（无 TTY），请用 `drama models --set %s=<choice>` 记录确认。" % a.kind)
        return EXIT_WAIT_CONFIRM
    kind = a.kind
    print("=" * 74)
    print("【前置条件】确认本项目的%s模型（工作区：%s）" % (KINDS[kind], ws))
    print("=" * 74)
    print(render_menu(kind))
    opts = MODEL_OPTIONS[kind]
    while True:
        raw = input("请输入序号 1-%d（q 退出）：" % len(opts)).strip()
        if raw.lower() in ("q", "quit", "exit"):
            print("[取消] 未记录任何确认。")
            return EXIT_WAIT_CONFIRM
        if raw.isdigit() and 1 <= int(raw) <= len(opts):
            chosen = opts[int(raw) - 1]
            confirm = input("确认选用「%s」？（y/N）：" % chosen["label"]).strip().lower()
            if confirm in ("y", "yes"):
                return _do_set(ws, kind, chosen["id"], {}, source="interactive")
            print("已取消本次选择，请重新输入。")
            continue
        print("输入无效，请重试。")


def cmd_require(a) -> int:
    ws = _resolve_ws(getattr(a, "workspace", None))
    ok, msg = require(ws, a.kind, expected_engine=getattr(a, "engine", None))
    if ok:
        print("[OK] %s" % msg)
        return 0
    print(render_confirm_request(a.kind, ws))
    print("[BLOCK] %s" % msg)
    return EXIT_WAIT_CONFIRM


def main() -> int:
    _harden_stdio()
    ap = argparse.ArgumentParser(description="生图/视频模型「首次执行前确认」门禁")
    ap.add_argument("--workspace", default=None, help="工作区（默认 DRAMA_WORKSPACE）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("list", help="打印可选模型清单")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("status", help="查看当前确认状态（只读）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("set", help="记录确认：set image=local_comfyui [video=runninghub]")
    p.add_argument("pairs", nargs="+", metavar="kind=choice")
    p.add_argument("--detail", default=None, help="附加说明（k=v,k2=v2 或 JSON）")
    p.set_defaults(fn=cmd_set)

    p = sub.add_parser("reset", help="清除某类确认（换模型前用）")
    p.add_argument("kind", choices=("image", "video"))
    p.set_defaults(fn=cmd_reset)

    p = sub.add_parser("ask", help="交互式引导确认（需 TTY）")
    p.add_argument("kind", nargs="?", default="image", choices=("image", "video"))
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("require", help="门禁查询（未过则退出码 4；供 drama.py 调用）")
    p.add_argument("kind", choices=("image", "video"))
    p.add_argument("--engine", default=None, help="本次实际使用的通道（local/rh/workflow）")
    p.set_defaults(fn=cmd_require)

    a = ap.parse_args()
    if not getattr(a, "fn", None):
        return cmd_status(a)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
