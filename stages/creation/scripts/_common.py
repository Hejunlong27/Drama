#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_common.py —— 创作层（stages/creation）共用工具：零第三方依赖

提供：控制台编码加固、JSON 读写、工作区解析、日期工具、创建产物目录约定。
所有创作层脚本（market_data / hit_breakdown / topic_analysis / hit_selection /
script_rewrite / creation）都从这里取公共件，避免各写一份。

流程（对应用户的四部分）
------------------------
    一 榜单调研与对比   market_data.py     S0  多市场榜单 + 横向对比（西语重点）
    二 爆款拆解         hit_breakdown.py   S1  题材类型/剧情套路/爆点元素/偏好/差异化趋势
    三 选题定位         topic_analysis.py  S2a 候选生成 + 七维评分 + 三维交叉
                        hit_selection.py   S2b 挑选 + 定位卡（受众画像/题材方向/差异化卖点）
    〇 剧本转译         script_translate.py S0b 小说/剧本 → 目标语种（多语种）+ 语言质检
    二·五 题材公式       genre_formula.py    S2.5 关键词→固定题材框架（可跳过选题）
    四 剧本改写         script_rewrite.py  S3  源剧本 → N 集规划（默认 40）+ FFS 骨架 + 自检
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import sys

# 分阶段目录真源（stages/_layout.py）：本层只保留历史相对路径写法，由它统一改写
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
try:
    import _layout as _L  # noqa: E402
except Exception:          # noqa: BLE001
    _L = None

# ---------------------------------------------------------------------------
# 目录约定（全部落在**工作区**，不进仓库）
# ---------------------------------------------------------------------------
CREATION = "creation"

MARKET_DIR = "creation/market"
BREAKDOWN_DIR = "creation/01-breakdown"
POSITIONING_DIR = "creation/02-positioning"
SCRIPT_DIR = "creation/03-script"

MARKET_FILE = MARKET_DIR + "/market-data.json"
BRIEF = CREATION + "/brief.json"

BREAKDOWN = BREAKDOWN_DIR + "/breakdown.json"

RAW_CANDIDATES = POSITIONING_DIR + "/candidates.raw.json"
CANDIDATES = POSITIONING_DIR + "/candidates.json"
POSITIONING = POSITIONING_DIR + "/positioning.json"

REFERENCE_STRUCTURE = SCRIPT_DIR + "/reference-structure.json"
SOURCE_SCRIPT = SCRIPT_DIR + "/source-script.md"
BLUEPRINT = SCRIPT_DIR + "/blueprint.json"
SCRIPT_BRIEF = SCRIPT_DIR + "/script-brief.json"
# ★ 2026-10-16 新增：转译（S0b）与题材公式（S2.5）的产物
SOURCE_SCRIPT_TRANSLATED = SCRIPT_DIR + "/source-script.%s.md"   # % 目标语短码（es/en/ja/…）
TRANSLATE_PLAN = SCRIPT_DIR + "/translate-plan.json"
TRANSLATION_BRIEF = SCRIPT_DIR + "/translation-brief.md"
GLOSSARY = SCRIPT_DIR + "/glossary.json"
GENRE_PLAN = SCRIPT_DIR + "/genre-plan.json"


def harden_stdio() -> None:
    """中文 Windows 控制台：TTY 用控制台代码页，管道/重定向用 UTF-8；errors=replace 兜底。"""
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


def read_json(p, default=None):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return default


def write_json(p, obj) -> pathlib.Path:
    p = pathlib.Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    # ★ newline=""：**强制 LF**。Windows 上 write_text 默认会把 \n 转成 \r\n，
    #   而仓库纪律是 LF（.gitattributes `* text=auto eol=lf`）—— 生成物（如 blueprint、
    #   由 JSON 生成的 md）若写成 CRLF，会把工作树弄脏、并与 manifest 口径不一致。
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")
    return p


def write_text(p, text: str) -> pathlib.Path:
    p = pathlib.Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", newline="")
    return p


def read_text(p) -> str:
    try:
        return pathlib.Path(p).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def resolve_ws(explicit=None) -> pathlib.Path:
    """工作区解析：`--workspace > SOLODRAMA_WORKSPACE > 项目标志物推断`（与 _ws.py 同口径）。"""
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
    # 脚本住在仓库里：向上找带 project.json / video-pipeline 的目录
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "project.json").is_file() or (parent / "video-pipeline").is_dir():
            return parent
    sys.exit("[FAIL] 找不到工作区。请用 --workspace 或环境变量 SOLODRAMA_WORKSPACE 指定。")


def pdir(ws, rel: str) -> pathlib.Path:
    """把历史相对路径解析成**实际路径**。

    走 `_layout.stage_rel` —— 有 `layout.json` 的项目会自动落到分阶段目录
    （`creation/market/x.json` → `<ws>/01-调研/x.json`）；旧项目原样不变。
    """
    if _L is not None:
        try:
            rel = _L.stage_rel(ws, rel)
        except Exception:      # noqa: BLE001
            pass
    return pathlib.Path(ws) / rel


def stage_dir(ws, key: str) -> pathlib.Path:
    """取某阶段目录的绝对路径（新布局）；旧布局下报可读错误。"""
    if _L is None:
        raise RuntimeError("stages/_layout.py 不可用")
    return _L.stage_dir(ws, key)


# ---------------------------------------------------------------------------
# 日期
# ---------------------------------------------------------------------------
def today() -> _dt.date:
    return _dt.date.today()


def parse_date(s) -> _dt.date | None:
    if not s:
        return None
    if isinstance(s, _dt.date):
        return s
    s = str(s).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def days(a: _dt.date, b: _dt.date) -> int:
    return (a - b).days


def now_iso() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
RULE = "=" * 74


def hr(ch: str = "=", n: int = 74) -> str:
    return ch * n


def emit(*lines) -> None:
    for ln in lines:
        print(ln)


def pct(x: float, total: float) -> float:
    return round(100.0 * x / total, 1) if total else 0.0
