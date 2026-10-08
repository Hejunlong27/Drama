# -*- coding: utf-8 -*-
"""_ws.py —— 工作区解析（把短剧生产脚本从「写死一个项目」变成「可移植」）

为什么要它（2026-09-20 封装 Skill 时发现的老问题）：
    同一批脚本里，工作区定位方式**三种混用**，复制到别处就跑不动：
      * `drama.py` / `gen_char_assets.py` / `gen_scene_assets.py` / `cost_report.py`
        —— 把 `<某个具体项目路径>` **硬编码**进去（本仓库已改为 repo 相对/环境变量）
      * `cost_ledger.py` / `episode_preflight.py` / `wire_*.py` / `_qa_subtitle_frame.py`
        —— 用 `__file__.parent.parent` 推断（只在「脚本住在 <项目>/scripts/ 下」时成立）
      * `subtitle.py` / `prepare_refs.py` —— 已经有 `--workspace`
    结果：脚本一旦离开这个项目目录就报「找不到 data/shots.json」之类的怪错。

解析优先级（高 → 低）：
    1. 函数参数 `explicit`（命令行 `--workspace` 传进来的值）
    2. 环境变量 `DRAMA_WORKSPACE`      ← 换项目/从 Skill 调用时用这个
    3. 脚本位置推断：`<项目>/scripts/xxx.py` ⇒ `<项目>`
    4. 报错并给出**可照抄的修法**（不静默猜路径）

用法：
    from _ws import WS          # 拿到 pathlib.Path
    from _ws import resolve     # 需要自己控制优先级时用
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 判定「这个目录就是短剧工作区」的标志物
MARKERS = (
    Path("video-pipeline") / "data" / "shots.json",
    Path("video-pipeline") / "data",
)

ENV_NAME = "DRAMA_WORKSPACE"

_HINT = (
    "[FAIL] 找不到短剧工作区。\n"
    "       请用下面任一方式指定（从高到低）：\n"
    "         ① 命令行加 --workspace \"<项目根目录>\"\n"
    "         ② 设环境变量：$env:{env} = \"<项目根目录>\"   （PowerShell）\n"
    "                      set {env}=<项目根目录>          （cmd）\n"
    "         ③ 或把脚本放在 <项目根>/scripts/ 下运行\n"
    "       工作区的标志物是 {mark}。"
).format(env=ENV_NAME, mark=str(MARKERS[0]))


def _looks_like_workspace(p: Path) -> bool:
    return any((p / m).exists() for m in MARKERS)


def resolve(explicit=None) -> Path:
    """按优先级解析工作区；解析不到直接退出并给出可照抄的修法。"""
    # ① 命令行显式指定
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_dir():
            return p.resolve()
        sys.exit("[FAIL] --workspace 指向的目录不存在：%s" % p)
    # ② 环境变量
    env = os.environ.get(ENV_NAME)
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p.resolve()
        sys.exit("[FAIL] 环境变量 %s 指向的目录不存在：%s" % (ENV_NAME, p))
    # ③ 脚本位置推断：<项目>/scripts/xxx.py
    cand = Path(__file__).resolve().parent.parent
    if _looks_like_workspace(cand):
        return cand
    # ④ 不猜，报错
    sys.exit(_HINT)


def resolve_lenient(explicit=None):
    """与 resolve 相同，但**不退出**：解析不到返回 None。

    给「只读、即使没有工作区也能跑」的脚本用（例如 rh_doctor 的双 Key 体检）。
    """
    if explicit:
        p = Path(explicit).expanduser()
        return p.resolve() if p.is_dir() else None
    env = os.environ.get(ENV_NAME)
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p.resolve()
    cand = Path(__file__).resolve().parent.parent
    return cand if _looks_like_workspace(cand) else None


# 绝大多数脚本直接用这个
WS: Path = resolve()

# 视频管线根（本项目约定：<工作区>/video-pipeline）
VP: Path = WS / "video-pipeline"
