# -*- coding: utf-8 -*-
"""_layout.py —— 工作区分阶段目录的**唯一真源**（2026-10-16）

为什么需要它
------------
此前工作区里「引擎目录 `video-pipeline/`」与「创作产物目录 `creation/`、`script/`、
`storyboard/`」平铺在根目录，阶段混在一起、语义不清。本模块把「一个剧项目的目录长什么样」
收敛成一处定义，并保证**向后兼容**：

  * 有 `<ws>/layout.json`  → 用新布局（分阶段编号目录）
  * 没有                   → 用旧布局（`video-pipeline/data` + `video-pipeline/output`）

因此**已存在的项目工作区零改动即可继续跑**。

新布局（`layout_version = 1`）
-----------------------------
    <剧项目>/
    ├── model-plan.json                 通道确认单（生图/视频 + 工作流来源）
    ├── project.json                    创作层状态
    ├── layout.json                     ← 本模块读的布局声明
    ├── 01-调研/                        市场数据 · 多市场对比 · 检索证据
    ├── 02-选题/                        候选池 · 七维评分 · 定位卡
    ├── 03-剧本/                        集数规划 · ep_XX.md · 台词时长审计
    ├── 04-资产/                        资产卡（人/场/道）· 参考图原图
    ├── 05-分镜/                        分镜脚本（11 字段）
    ├── 06-提示词/                      H3 六段式提示词 + data/ 四文件 + refs_h3/
    ├── 07-成片/                        逐段成片 · 母版 · 2K · 字幕
    ├── 08-合规/                        发布前预检 · 整改记录
    ├── 09-发行/                        封面 · 文案 · 发布清单
    └── video-pipeline/                 引擎技术目录（config.*.json / ledger / logs）

设计约束
--------
1. **不重复存放**：同一份产物只在一个阶段目录里；阶段之间靠「引用」而不是「复制」衔接。
2. **引擎口径不变**：引擎侧仍认为 `<engine_root>/config.local.json` 是唯一可写配置；
   `data_dir` / `out_dir` 用引擎**已支持**的覆盖键指到阶段目录（相对 `engine_root`）。
3. **只读派生**：`layout.json` 缺失/损坏 → 退回旧布局并警告，不抛异常。
"""
from __future__ import annotations

import json
import os
import pathlib
from pathlib import Path

LAYOUT_FILE = "layout.json"
LAYOUT_VERSION = 1

# 旧布局（默认值）—— 与 2026-10-16 之前的行为完全一致
LEGACY_ENGINE_ROOT = "video-pipeline"
LEGACY_DATA_REL = "video-pipeline/data"
LEGACY_OUT_REL = "video-pipeline/output"

# 阶段键 → 默认目录名（新布局）
STAGE_DIRS: dict = {
    "research": "01-调研",
    "topic": "02-选题",
    "script": "03-剧本",
    "assets": "04-资产",
    "storyboard": "05-分镜",
    "prompts": "06-提示词",
    "film": "07-成片",
    "compliance": "08-合规",
    "release": "09-发行",
}

# 各阶段目录的唯一用途（供脚手架写 README、供 layout 子命令展示）
STAGE_PURPOSE: dict = {
    "research": "市场调研：market-data.json（多市场榜单/对标剧/趋势）、多市场对比表、检索证据",
    "topic": "选题定位：candidates.json（七维评分）、positioning.json（定位卡）",
    "script": "剧本：blueprint.json/md（集数规划）、ep_XX.md（单集）、台词时长审计报告",
    "assets": "资产：资产卡（人物/场景/道具 md）、参考图原图（refs/）",
    "storyboard": "分镜：storyboard/epXX.md（11 字段分镜脚本，含 <Picture N> 与 [Shot N]）",
    "prompts": "提示词：H3 六段式提示词 md + data/ 四文件（出片引擎唯一输入契约）+ refs_h3/",
    "film": "成片：逐段 final.mp4、整集母版、2K、字幕成片、样张",
    "compliance": "合规：发布前预检报告、TikTok 条款映射、整改记录、申诉材料",
    "release": "发行：封面（PNG/JPG/preview）、文案（六槽位 + 三档描述）、发布清单",
}

DEFAULT_LAYOUT: dict = {
    "layout_version": LAYOUT_VERSION,
    "engine_root": LEGACY_ENGINE_ROOT,
    "stages": dict(STAGE_DIRS),
    # ⚠️ data_rel / out_rel 一律是**相对 engine_root** 的路径（与引擎 `_resolve_against` 同语义）。
    #    旧布局下 engine_root 已经是 <ws>/video-pipeline，所以这里是 "data" / "output"，
    #    **不是** "video-pipeline/data" —— 后者会拼成 video-pipeline/video-pipeline/data。
    "data_rel": "data",
    "out_rel": "output",
}


def _norm_ws(ws) -> Path:
    return Path(ws).expanduser().resolve()


def load(ws) -> dict:
    """读 `<ws>/layout.json`；缺失或非法 → 返回旧布局（并带 `_fallback=True`）。"""
    ws = _norm_ws(ws)
    p = ws / LAYOUT_FILE
    if not p.is_file():
        d = dict(DEFAULT_LAYOUT)
        d["stages"] = dict(STAGE_DIRS)
        d["_fallback"] = True
        return d
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, ValueError):
        d = dict(DEFAULT_LAYOUT)
        d["stages"] = dict(STAGE_DIRS)
        d["_fallback"] = True
        d["_error"] = "layout.json 无法解析，已回退旧布局"
        return d
    if not isinstance(raw, dict):
        d = dict(DEFAULT_LAYOUT)
        d["stages"] = dict(STAGE_DIRS)
        d["_fallback"] = True
        d["_error"] = "layout.json 顶层应为对象，已回退旧布局"
        return d

    out = dict(DEFAULT_LAYOUT)
    out["stages"] = dict(STAGE_DIRS)
    out.update({k: v for k, v in raw.items() if k in
                ("layout_version", "engine_root", "data_rel", "out_rel")})
    st = raw.get("stages")
    if isinstance(st, dict):
        for k, v in st.items():
            if k in STAGE_DIRS and isinstance(v, str) and v.strip():
                out["stages"][k] = v.strip()
    out["_fallback"] = False
    return out


def is_new_layout(ws) -> bool:
    return not load(ws).get("_fallback", True)


def engine_root(ws) -> Path:
    """引擎技术目录（config.*.json / ledger / logs 所在）。"""
    return _norm_ws(ws) / load(ws).get("engine_root") or LEGACY_ENGINE_ROOT


def stage_dir(ws, key: str) -> Path:
    """某阶段的目录（绝对路径）。"""
    d = load(ws)["stages"].get(key)
    if not d:
        raise KeyError("未知阶段键：%s（可选 %s）" % (key, list(STAGE_DIRS)))
    return _norm_ws(ws) / d


def all_stage_dirs(ws) -> dict:
    """{阶段键: 绝对路径}，按 STAGE_DIRS 顺序。"""
    lay = load(ws)
    ws = _norm_ws(ws)
    return {k: ws / lay["stages"][k] for k in STAGE_DIRS if k in lay["stages"]}


def data_dir(ws) -> Path:
    """出片引擎的**输入目录**（含 shots.json / characters.json / scenes.json / h3_prompts.json）。

    判定顺序（与引擎口径保持一致）：
      ① `<engine_root>/config.local.json` 的 `data_dir`（引擎自己的覆盖层，优先）
      ② `<ws>/layout.json` 的 `data_rel`
      ③ 旧默认 `video-pipeline/data`

    ①②里的相对路径一律**相对 `engine_root`** 解析（这就是引擎 `_resolve_against` 的语义）。
    """
    return _resolve_rel(ws, "data_dir", "data_rel", LEGACY_DATA_REL)


def out_dir(ws) -> Path:
    """出片引擎的**输出根目录**（批次目录 / 资产图 / subs 都在它下面）。"""
    return _resolve_rel(ws, "out_dir", "out_rel", LEGACY_OUT_REL)


# 创作层/工具链的历史相对路径 → layout 阶段键（前缀匹配）
LEGACY_STAGE_MAP: dict = {
    "creation/market/": "research",
    "creation/01-breakdown/": "research",
    "creation/02-positioning/": "topic",
    "creation/03-script/": "script",
    "bible/": "script",
    "script/": "script",
    "storyboard/": "storyboard",
    "prompts/": "prompts",
    "refs/": "assets",
}

# 需要提到工作区根的项目级文件（不属于任何阶段）
ROOT_LEVEL_FILES = ("creation/brief.json",)


def stage_rel(ws, rel: str) -> str:
    """把历史相对路径映射到新布局的阶段相对路径。

    * 有 `layout.json`  → 映射（`creation/market/x.json` → `01-调研/x.json`）
    * 无（旧布局）      → 原样返回，行为完全不变

    存在的意义：让创作层与工具链**不必知道自己住在新还是旧布局**，
    只保留原来的相对路径写法，由这一处统一改写。
    """
    rel = str(rel).replace("\\", "/").lstrip("/")
    lay = load(ws)
    if lay.get("_fallback"):
        return rel
    for f in ROOT_LEVEL_FILES:
        if rel == f:
            return f.split("/")[-1]
    for pre, key in LEGACY_STAGE_MAP.items():
        # ★ 2026-10-16 修正：前缀表里的键带尾斜杠，只匹配「文件级」路径，
        #   导致「目录级」调用（如 `pdir(ws, "creation/01-breakdown")` 后 mkdir）
        #   漏映射、在新布局里凭空建出空的 `creation/` 目录。
        #   改为「边界感知」匹配：整体相等（目录级）或 `前缀 + /`（文件级）。
        p = pre.rstrip("/")
        if rel == p:
            return lay["stages"][key]
        if rel.startswith(p + "/"):
            return "%s/%s" % (lay["stages"][key], rel[len(p) + 1:])
    return rel


def _resolve_rel(ws, overlay_key: str, layout_key: str, fallback_rel: str) -> Path:
    ws = _norm_ws(ws)
    er = engine_root(ws)
    rel = None
    local = er / "config.local.json"
    if local.is_file():
        try:
            cfg = json.loads(local.read_text(encoding="utf-8-sig"))
            if isinstance(cfg, dict) and isinstance(cfg.get(overlay_key), str) and cfg[overlay_key].strip():
                rel = cfg[overlay_key].strip()
        except (OSError, json.JSONDecodeError, ValueError):
            rel = None
    if rel is None:
        rel = load(ws).get(layout_key) or fallback_rel
    p = Path(rel).expanduser()
    return p.resolve() if p.is_absolute() else (er / p).resolve()


# ---------------------------------------------------------------------------
# 脚手架：按新布局建目录 + 写 layout.json + 各阶段 README
# ---------------------------------------------------------------------------
def scaffold(ws, *, engine_root_name: str = LEGACY_ENGINE_ROOT) -> dict:
    """建新布局目录骨架，写 `layout.json`，并回填 `config.local.json` 的 data/out 覆盖键。

    返回 {created:[...], layout: <dict>, engine_root: Path}
    """
    ws = _norm_ws(ws)
    ws.mkdir(parents=True, exist_ok=True)
    created = []

    # 1) 阶段目录（编号 = 执行顺序）
    for key in STAGE_DIRS:
        d = ws / STAGE_DIRS[key]
        if not d.exists():
            created.append(str(d))
        d.mkdir(parents=True, exist_ok=True)
        readme = d / "README.md"
        if not readme.exists():
            readme.write_text(
                "# %s\n\n%s\n\n> 本目录由 `stages/_layout.py` 定义为「%s」阶段的**唯一**落点；\n"
                "> 其他阶段不得在此写入同名文件。\n" % (STAGE_DIRS[key], STAGE_PURPOSE[key], key),
                encoding="utf-8")

    # 2) 引擎技术目录（config / ledger / logs）
    er = ws / engine_root_name
    for sub in ("", "ledger/runs", "logs"):
        p = er / sub if sub else er
        if not p.exists():
            created.append(str(p))
        p.mkdir(parents=True, exist_ok=True)

    # 3) layout.json（data_rel / out_rel 一律**相对 engine_root**）
    er_name = er.name
    data_rel = "../%s" % STAGE_DIRS["prompts"]
    out_rel = "../%s" % STAGE_DIRS["film"]
    lay = {
        "layout_version": LAYOUT_VERSION,
        "engine_root": er_name,
        "stages": dict(STAGE_DIRS),
        "data_rel": data_rel,
        "out_rel": out_rel,
        "_comment": ("阶段目录为各阶段唯一落点；data_rel / out_rel 是**相对 engine_root** 的"
                     "输入/输出目录，会被回填进 <engine_root>/config.local.json 的 data_dir / out_dir。"),
    }
    lp = ws / LAYOUT_FILE
    lp.write_text(json.dumps(lay, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 4) config.local.json 回填 data_dir / out_dir（相对 engine_root）
    _patch_local_cfg(er, {
        "data_dir": "../%s" % STAGE_DIRS["prompts"],
        "out_dir": "../%s" % STAGE_DIRS["film"],
    })
    return {"created": created, "layout": lay, "engine_root": er, "layout_file": lp}


def _patch_local_cfg(engine_root: Path, patch: dict) -> Path:
    """把 data_dir / out_dir 等键合并进 `<engine_root>/config.local.json`（不覆盖其他键）。"""
    engine_root.mkdir(parents=True, exist_ok=True)
    p = engine_root / "config.local.json"
    cfg = {}
    if p.is_file():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8-sig")) or {}
        except (OSError, json.JSONDecodeError, ValueError):
            cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.setdefault("_comment", "ai-video-pipeline 用户私有配置覆盖层（由 _layout.scaffold 回填阶段目录）")
    cfg.update(patch)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def env_workspace():
    """与 `_ws.py` 同口径：SOLODRAMA_WORKSPACE 优先，回退 DRAMA_WORKSPACE。"""
    for n in ("SOLODRAMA_WORKSPACE", "DRAMA_WORKSPACE"):
        v = os.environ.get(n)
        if v:
            return v
    return None


def locate_repo_stages() -> Path | None:
    """从本文件位置向上找 `stages/` 目录（供其他脚本 sys.path 引导用）。"""
    p = Path(__file__).resolve()
    for parent in p.parents:
        if parent.name == "stages" and (parent / "_layout.py").is_file():
            return parent
    return None


def ensure_importable() -> bool:
    """把 `stages/` 加进 sys.path（幂等）。返回是否成功。"""
    import sys
    d = locate_repo_stages()
    if not d:
        return False
    s = str(d)
    if s not in sys.path:
        sys.path.insert(0, s)
    return True


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="_layout —— 工作区分阶段目录查询")
    ap.add_argument("--workspace", default=None)
    a = ap.parse_args()
    ws = a.workspace or env_workspace() or "."
    lay = load(ws)
    print("工作区   : %s" % _norm_ws(ws))
    print("布局版本 : %s%s" % (lay.get("layout_version"), "（旧布局回退）" if lay.get("_fallback") else ""))
    print("引擎根   : %s" % engine_root(ws))
    print("输入目录 : %s" % data_dir(ws))
    print("输出目录 : %s" % out_dir(ws))
    print("-" * 60)
    for k, d in all_stage_dirs(ws).items():
        print("  %-11s %-12s %s" % (k, d.name, "[存在]" if d.exists() else "[缺失]"))
