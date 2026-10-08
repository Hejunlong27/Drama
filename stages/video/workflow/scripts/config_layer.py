# -*- coding: utf-8 -*-
"""
config_layer.py —— ai-video-pipeline 脚本层共享库（非 CLI）

职责：
  1. 解析 ENGINE 目录（脚本层 -> 引擎真源）
  2. 解析 WORKSPACE（工作区，4 级优先级，与引擎 main.py 完全一致）
  3. 读写 <工作区>/config.local.json（唯一允许脚本层写入的配置）
  4. 加载引擎 config 模块并应用覆盖层
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent
DEFAULT_ENGINE_DIR = SCRIPTS_DIR / "engine"


# ===========================================================================
# UTF-8 输出（Windows 控制台默认 GBK，必须显式声明）
# ===========================================================================
def force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


# ===========================================================================
# 路径解析
# ===========================================================================
def resolve_engine() -> Path:
    """引擎目录：环境变量 VIDEO_PIPELINE_ENGINE > <skill>/scripts/engine。"""
    env = os.getenv("VIDEO_PIPELINE_ENGINE", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_ENGINE_DIR.resolve()


def resolve_workspace(explicit: Optional[str] = None) -> Path:
    """工作区（高 -> 低）：
    ① 显式参数 ② 环境变量 VIDEO_PIPELINE_WORKSPACE ③ <engine>/.workspace 指针
    ④ ~/video-pipeline-workspace
    """
    for cand in (explicit, os.getenv("VIDEO_PIPELINE_WORKSPACE")):
        if cand and str(cand).strip():
            return Path(str(cand)).expanduser().resolve()
    ptr = resolve_engine() / ".workspace"
    if ptr.is_file():
        line = ptr.read_text(encoding="utf-8-sig").strip()
        if line:
            return Path(line).expanduser().resolve()
    return (Path.home() / "video-pipeline-workspace").resolve()


def workspace_is_inside_skill(ws: Path) -> bool:
    """工作区是否落在 skill 包内（会被 SkillHub 更新覆盖，必须警告）。"""
    try:
        ws.resolve().relative_to(SKILL_DIR.resolve())
        return True
    except ValueError:
        return False


# ===========================================================================
# config.local.json 读写
# ===========================================================================
def overlay_path(workspace: Path) -> Path:
    return workspace / "config.local.json"


def read_overlay(workspace: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """读覆盖层。返回 (数据, 错误信息)；文件不存在返回 (None, None)。"""
    p = overlay_path(workspace)
    if not p.is_file():
        return None, None
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as e:
        return None, f"{p.name} 无法解析: {e}"
    if not isinstance(data, dict):
        return None, f"{p.name} 顶层应为 JSON 对象"
    return data, None


def write_overlay(workspace: Path, data: Dict[str, Any]) -> Path:
    """原子写覆盖层（UTF-8 无 BOM）。"""
    p = overlay_path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ===========================================================================
# 引擎配置加载
# ===========================================================================
def load_engine_config(workspace: Optional[Path] = None):
    """把引擎目录加入 sys.path，导入 config 并应用覆盖层。返回 config 模块。"""
    engine = resolve_engine()
    if str(engine) not in sys.path:
        sys.path.insert(0, str(engine))
    cfg = importlib.import_module("config")
    if workspace is not None:
        cfg.apply_overlay(overlay_path(workspace))
    return cfg


def mask_key(value: str) -> str:
    v = str(value or "")
    if not v:
        return "(空)"
    if len(v) <= 6:
        return "*" * len(v)
    return f"{v[:3]}****{v[-4:]}"
