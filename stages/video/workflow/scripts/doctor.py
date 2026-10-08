# -*- coding: utf-8 -*-
"""
doctor.py —— 环境体检（G0，不联网）

用法：
    python run.py doctor                # 人读报告
    python run.py doctor --json         # 机器可读（供 Agent 解析）
    python run.py doctor --workspace <路径>

退出码：0 全部通过；1 存在 FAIL；2 体检本身异常
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_layer import (          # noqa: E402
    force_utf8_stdio, resolve_engine, resolve_workspace, read_overlay,
    overlay_path, mask_key, workspace_is_inside_skill, SCRIPTS_DIR,
)

force_utf8_stdio()

ENGINE_FILES = ["config.py", "main.py", "pipeline.py", "runninghub_client.py",
                "tools/probe_nodes.py", "requirements.txt"]

CHECK_IDS = ["python_version", "deps", "engine_files", "workspace", "overlay_file",
             "api_key", "provider_id", "h3_id", "nodes", "input_data"]


def _sha8(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    except OSError:
        return "unreadable"


def _run(args) -> dict:
    checks: list = []

    def add(cid: str, level: str, detail: str, fix: str = "") -> None:
        checks.append({"id": cid, "level": level, "detail": detail, "fix": fix})

    # ---- 1) Python 版本 ----------------------------------------------------
    v = sys.version_info
    if (v.major, v.minor) >= (3, 10):
        add("python_version", "PASS", f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        add("python_version", "FAIL", f"Python {v.major}.{v.minor} 低于要求的 3.10",
            "改用 Python 3.10+ 解释器（建议 3.13）")

    # ---- 2) 依赖 -----------------------------------------------------------
    dep_parts, missing_dep = [], []
    for mod, label in (("httpx", "httpx"), ("PIL", "Pillow")):
        try:
            m = __import__(mod)
            dep_parts.append(f"{label} {getattr(m, '__version__', '?')}")
        except Exception:                            # noqa: BLE001
            missing_dep.append(label)
    if missing_dep:
        add("deps", "FAIL", f"缺少依赖: {', '.join(missing_dep)}",
            "pip install -r <repo>/stages/video/workflow/scripts/engine/requirements.txt")
    else:
        add("deps", "PASS", " / ".join(dep_parts))

    # ---- 3) 引擎文件 -------------------------------------------------------
    engine = resolve_engine()
    miss_files = []
    heads = {}
    for rel in ENGINE_FILES:
        p = engine / rel
        if p.is_file():
            heads[rel] = _sha8(p)
        else:
            miss_files.append(rel)
    if miss_files:
        add("engine_files", "FAIL", f"引擎目录 {engine} 缺文件: {', '.join(miss_files)}",
            f"检查 VIDEO_PIPELINE_ENGINE 是否指错；默认应为 {SCRIPTS_DIR / 'engine'}")
    else:
        add("engine_files", "PASS", f"引擎 {engine} | sha256 前 8: "
            + ", ".join(f"{k}={v}" for k, v in list(heads.items())[:3]) + " ...")

    # ---- 4) 工作区 ---------------------------------------------------------
    ws = resolve_workspace(args.workspace)
    ws_probe_ok = True
    try:
        ws.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="doctor_", dir=str(ws))
        os.close(fd)                 # 先关句柄再删，否则 Windows 报 WinError 32
        Path(name).unlink()
    except OSError as e:
        ws_probe_ok = False
        add("workspace", "FAIL", f"工作区不可写: {ws} ({e})",
            "检查磁盘权限，或用 --workspace / 环境变量 VIDEO_PIPELINE_WORKSPACE 换目录")
    if ws_probe_ok:
        inside = workspace_is_inside_skill(ws)
        add("workspace", "WARN" if inside else "PASS",
            f"工作区 {ws}" + ("（★ 落在 Skill 包内，更新会被覆盖，强烈建议迁出）" if inside else ""),
            "configure.py set-workspace <新路径>" if inside else "")

    # ---- 5) 覆盖层 ---------------------------------------------------------
    data, err = read_overlay(ws)
    if err:
        add("overlay_file", "FAIL", err,
            f"修正 {overlay_path(ws)} 的 JSON 语法（可对照 assets/config.local.example.json）")
    elif data is None:
        add("overlay_file", "WARN", f"尚未创建 {overlay_path(ws)}",
            "configure.py init 或 copy assets/config.local.example.json")
    else:
        add("overlay_file", "PASS", f"{overlay_path(ws)} 可解析（{len(data)} 个顶层键）")

    # ---- 6-9) 配置齐备度 ---------------------------------------------------
    cfg = None
    try:
        sys.path.insert(0, str(engine))
        import config as C                        # noqa: E402
        cfg = C
        warns = C.apply_overlay(overlay_path(ws))
        for w in warns:
            add("overlay_file", "WARN", w)
    except Exception as e:                        # noqa: BLE001
        add("engine_files", "FAIL", f"引擎 config 无法导入: {e}")

    if cfg is not None:
        key = cfg.RUNNINGHUB_API_KEY
        add("api_key", "PASS" if key else "FAIL",
            f"RUNNINGHUB_API_KEY = {mask_key(key)}"
            + ("（来自环境变量）" if os.getenv("RUNNINGHUB_API_KEY") else ""),
            "" if key else "告诉我“帮我配置环境变量 RUNNINGHUB_API_KEY”，"
                          "或 configure.py set-key --value <key>")

        prov = cfg.IMAGE_PROVIDERS.get(cfg.ACTIVE_IMAGE_PROVIDER) or {}
        add("provider_id", "PASS" if prov.get("id") else "FAIL",
            f"图片模型 [{cfg.ACTIVE_IMAGE_PROVIDER}] id = {prov.get('id') or '(未填写)'}",
            "" if prov.get("id") else "configure.py set-provider --provider "
                                     f"{cfg.ACTIVE_IMAGE_PROVIDER} --id <workflowId>")

        add("h3_id", "PASS" if cfg.H3.get("id") else "FAIL",
            f"H3 工作流 id = {cfg.H3.get('id') or '(未填写)'}",
            "" if cfg.H3.get("id") else "configure.py set-h3 --id <workflowId>")

        miss_cfg = cfg.missing_configs()
        node_miss = [m for m in miss_cfg if ".nodes." in m]
        other_miss = [m for m in miss_cfg if ".nodes." not in m
                      and not m.startswith("RUNNINGHUB_API_KEY")
                      and ".id（" not in m and not m.startswith("h3.id")]
        if not node_miss:
            add("nodes", "PASS", "必填节点映射齐全（prompt / 首帧等）")
        else:
            add("nodes", "FAIL", f"缺 {len(node_miss)} 个必填节点: {node_miss}",
                "run.py probe --target banana --emit probe.json "
                "→ 确认候选 → configure.py apply-nodes --from probe.json --target banana")
        if other_miss:
            add("provider_id", "WARN", "其它未填配置: " + "; ".join(other_miss))

    # ---- 10) 输入数据 ------------------------------------------------------
    if cfg is not None:
        try:
            sys.path.insert(0, str(engine))
            import main as engine_main              # noqa: E402
            data_dir = engine_main._resolve_against(ws, cfg.DATA_DIR)
            problems = engine_main.Inputs(data_dir).validate()
            if problems:
                add("input_data", "FAIL", f"{data_dir} 有 {len(problems)} 个问题: "
                    + "; ".join(problems[:5]) + ("..." if len(problems) > 5 else ""),
                    "按提示修正 shots.json / characters.json / scenes.json / h3_prompts.json")
            else:
                n = len(engine_main.Inputs(data_dir).shots)
                add("input_data", "PASS", f"{data_dir} 校验通过（{n} 个分镜）")
        except Exception as e:                      # noqa: BLE001
            add("input_data", "FAIL", f"输入数据校验异常: {e}")

    # ---- next_action -------------------------------------------------------
    by_id = {c["id"]: c for c in checks}
    next_action = "run.py --dry-run --strict（G1 预演）"
    if by_id.get("workspace", {}).get("level") == "FAIL":
        next_action = "先解决工作区问题（不可写）"
    elif by_id.get("overlay_file", {}).get("level") == "FAIL":
        next_action = "先修正 config.local.json 的 JSON 语法"
    elif by_id.get("api_key", {}).get("level") == "FAIL":
        next_action = "配置 RUNNINGHUB_API_KEY（环境变量优先）"
    elif by_id.get("provider_id", {}).get("level") in ("FAIL", "WARN"):
        next_action = "configure.py set-provider <图片工作流 ID>"
    elif by_id.get("h3_id", {}).get("level") == "FAIL":
        next_action = "configure.py set-h3 <H3 工作流 ID>"
    elif by_id.get("nodes", {}).get("level") == "FAIL":
        next_action = "run.py probe --target banana --emit probe.json → configure.py apply-nodes"
    elif by_id.get("input_data", {}).get("level") == "FAIL":
        next_action = "修正 data/ 输入 JSON"
    elif by_id.get("workspace", {}).get("level") == "WARN":
        next_action = "工作区在 Skill 包内，建议迁出后再跑"

    return {
        "ok": all(c["level"] != "FAIL" for c in checks),
        "python": f"{v.major}.{v.minor}.{v.micro}",
        "engine_path": str(engine),
        "engine_sha8": heads,
        "workspace": str(ws),
        "checks": checks,
        "next_action": next_action,
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ai-video-pipeline 环境体检（G0）")
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        report = _run(args)
    except Exception as e:                          # noqa: BLE001
        print(f"[FAIL] 体检异常: {e}")
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("=" * 78)
        print("ai-video-pipeline 环境体检（G0）")
        print("=" * 78)
        print(f"引擎   : {report['engine_path']}")
        print(f"工作区 : {report['workspace']}")
        print("-" * 78)
        for c in report["checks"]:
            mark = {"PASS": "[OK]  ", "WARN": "[WARN]", "FAIL": "[FAIL]"}[c["level"]]
            print(f"{mark} {c['id']:<16} {c['detail']}")
            if c["level"] != "PASS" and c["fix"]:
                print(f"{'':<23}-> {c['fix']}")
        print("-" * 78)
        print(f"下一步 : {report['next_action']}")
        print("=" * 78)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
