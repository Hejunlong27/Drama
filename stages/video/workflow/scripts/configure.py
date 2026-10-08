# -*- coding: utf-8 -*-
"""
configure.py —— 工作区初始化 + 配置回填（只写 <工作区>/config.local.json，绝不碰 config.py）

子命令：
    init       --workspace <路径>                    建工作区（data/output/refs + 样例 + .workspace 指针）
    set-key    --value <key> | --from-env            写 API Key（建议直接配环境变量，本命令是兜底）
    set-workspace <路径>                             更新 .workspace 指针
    set-provider --provider banana --kind workflow --id <ID>
    set-h3       --kind workflow --id <ID> [--frame-source cell_01|grid_raw]
    apply-nodes  --from probe.json --target banana|h3 [--yes]
    set          --key a.b.c --value <v>             按点路径写白名单键
    unset        --key a.b.c
    show                                             打印生效配置（Key 打码 + 层级回执）
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_layer import (          # noqa: E402
    force_utf8_stdio, resolve_engine, resolve_workspace, read_overlay,
    write_overlay, load_engine_config, mask_key, workspace_is_inside_skill,
    SKILL_DIR, SCRIPTS_DIR,
)

force_utf8_stdio()

SET_KEY_WHITELIST = {
    "runninghub_api_key", "runninghub_base_url", "http_timeout",
    "active_image_provider", "concurrency", "batch_size", "poll_interval",
    "poll_channel", "image_timeout_min", "video_timeout_min", "retry_max",
    "retry_backoff", "error_text_limit", "fuse_consecutive_fails",
    "fuse_fail_rate", "fuse_min_samples", "data_dir", "out_dir",
    "upload_cache_ttl_hours", "keep_raw_zip",
}
NODE_TARGETS = {"banana": "image_providers", "h3": "h3"}


# ===========================================================================
# init
# ===========================================================================
def cmd_init(args) -> int:
    ws = Path(args.workspace).expanduser().resolve() if args.workspace else resolve_workspace()
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "data").mkdir(exist_ok=True)
    (ws / "output").mkdir(exist_ok=True)
    (ws / "refs").mkdir(exist_ok=True)

    tpl = SKILL_DIR / "assets" / "workspace-template" / "data"
    copied = []
    if tpl.is_dir():
        for f in sorted(tpl.iterdir()):
            dst = ws / "data" / f.name
            if not dst.exists():
                shutil.copy2(f, dst)
                copied.append(f.name)

    ptr = resolve_engine() / ".workspace"
    ptr.write_text(str(ws) + "\n", encoding="utf-8")

    cfg_local = ws / "config.local.json"
    if not cfg_local.exists():
        example = SKILL_DIR / "assets" / "config.local.example.json"
        if example.is_file():
            shutil.copy2(example, cfg_local)

    print(f"[OK] 工作区: {ws}")
    print(f"[OK] .workspace 指针 -> {ptr}")
    print(f"[OK] 铺样例: {', '.join(copied) if copied else '（已有文件，未覆盖）'}")
    print(f"[OK] 覆盖层: {cfg_local}{'（新建，全是空值）' if cfg_local.exists() and not copied else ''}")
    if workspace_is_inside_skill(ws):
        print("[WARN] 工作区落在 Skill 包内，Skill 更新会被覆盖，建议迁出！")
    print("下一步: run.py doctor --json")
    return 0


def cmd_set_workspace(args) -> int:
    ws = Path(args.path).expanduser().resolve()
    ws.mkdir(parents=True, exist_ok=True)
    ptr = resolve_engine() / ".workspace"
    ptr.write_text(str(ws) + "\n", encoding="utf-8")
    print(f"[OK] .workspace 指针 -> {ws}")
    return 0


# ===========================================================================
# 写覆盖层的工具函数
# ===========================================================================
def _set_nested(data: Dict[str, Any], path: List[str], value: Any) -> None:
    cur = data
    for key in path[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[path[-1]] = value


def _del_nested(data: Dict[str, Any], path: List[str]) -> bool:
    cur = data
    for key in path[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            return False
        cur = nxt
    return cur.pop(path[-1], None) is not None


def _load_for_write(workspace: Path) -> Dict[str, Any]:
    data, err = read_overlay(workspace)
    if err:
        print(f"[FAIL] {err}")
        raise SystemExit(1)
    return data or {}


def _finish(workspace: Path, data: Dict[str, Any], note: str) -> int:
    p = write_overlay(workspace, data)
    print(f"[OK] {note}")
    print(f"[OK] 已写入 {p}")
    print("验证: run.py check   或   configure.py show")
    return 0


# ===========================================================================
# 子命令
# ===========================================================================
def cmd_set_key(args) -> int:
    ws = resolve_workspace(args.workspace)
    value = args.value
    if args.from_env:
        import os
        value = os.getenv(args.from_env, "")
        if not value:
            print(f"[FAIL] 环境变量 {args.from_env} 为空")
            return 1
    if not value:
        print("[FAIL] 缺 --value <key> 或 --from-env <VAR>。"
              "更推荐：让 Agent 帮你配置环境变量 RUNNINGHUB_API_KEY（不落盘）。")
        return 1
    data = _load_for_write(ws)
    data["runninghub_api_key"] = value
    return _finish(ws, data, "API Key 已写入（环境变量存在时会被忽略，环境变量优先）")


def cmd_set_provider(args) -> int:
    ws = resolve_workspace(args.workspace)
    data = _load_for_write(ws)
    prov = data.setdefault("image_providers", {}).setdefault(args.provider, {})
    if args.kind:
        prov["kind"] = args.kind
    if args.id:
        prov["id"] = str(args.id).strip()
    if args.label:
        prov["label"] = args.label
    if not prov.get("id"):
        print("[FAIL] 缺 --id <workflowId>")
        return 1
    data.setdefault("active_image_provider", args.provider)
    return _finish(ws, data,
                   f"image_providers.{args.provider}.id = {prov['id']} "
                   f"(kind={prov.get('kind', 'workflow')})")


def cmd_set_h3(args) -> int:
    ws = resolve_workspace(args.workspace)
    data = _load_for_write(ws)
    h3 = data.setdefault("h3", {})
    if args.kind:
        h3["kind"] = args.kind
    if args.frame_source:
        h3["frame_source"] = args.frame_source
    if args.id:
        h3["id"] = str(args.id).strip()
    if not h3.get("id"):
        print("[FAIL] 缺 --id <workflowId>")
        return 1
    return _finish(ws, data, f"h3.id = {h3['id']} (kind={h3.get('kind', 'workflow')})")


def cmd_apply_nodes(args) -> int:
    ws = resolve_workspace(args.workspace)
    src = Path(args.src).expanduser()
    if not src.is_absolute():
        src = ws / src
    if not src.is_file():
        print(f"[FAIL] 找不到探测结果文件: {src}（先 run.py probe --emit <文件>）")
        return 1
    payload = json.loads(src.read_text(encoding="utf-8-sig"))
    frag = (payload.get("candidates") or {}).get(args.target)
    if not frag:
        print(f"[FAIL] {src} 里没有 target={args.target} 的候选片段")
        return 1

    nodes = frag.get("nodes") or {}
    low_conf = [(k, v.get("confidence")) for k, v in nodes.items()
                if isinstance(v, dict) and v.get("confidence") in ("low", "none")]
    where = ("image_providers[active_image_provider].nodes" if args.target == "banana"
             else "h3.nodes")
    print(f"将把以下节点映射写入 {where}：")
    print(json.dumps(nodes, ensure_ascii=False, indent=2))
    if low_conf:
        print(f"[WARN] 有 {len(low_conf)} 项置信度低/未探测到: {low_conf}，请逐项人工核对！")
    if not args.yes:
        print("\n[SKIP] 未回填。确认无误后加 --yes 重跑本命令。")
        return 2

    data = _load_for_write(ws)
    if args.target == "banana":
        prov = data.setdefault("image_providers", {}).setdefault(
            payload.get("target_provider") or data.get("active_image_provider", "banana"), {})
        prov.setdefault("kind", frag.get("kind", "workflow"))
        prov["id"] = frag.get("id") or prov.get("id", "")
        prov["nodes"] = {k: {kk: vv for kk, vv in v.items() if kk != "confidence"}
                         for k, v in nodes.items() if isinstance(v, dict)}
    else:
        h3 = data.setdefault("h3", {})
        h3.setdefault("kind", frag.get("kind", "workflow"))
        h3["id"] = frag.get("id") or h3.get("id", "")
        if frag.get("frame_source"):
            h3["frame_source"] = frag["frame_source"]
        h3["nodes"] = {k: {kk: vv for kk, vv in v.items() if kk != "confidence"}
                       for k, v in nodes.items() if isinstance(v, dict)}
    return _finish(ws, data, f"{args.target} 节点映射已回填")


def cmd_set(args) -> int:
    ws = resolve_workspace(args.workspace)
    if args.key not in SET_KEY_WHITELIST:
        print(f"[FAIL] '{args.key}' 不在白名单里。允许: {sorted(SET_KEY_WHITELIST)}")
        return 1
    try:
        value = json.loads(args.value)
    except json.JSONDecodeError:
        value = args.value
    data = _load_for_write(ws)
    data[args.key] = value
    return _finish(ws, data, f"{args.key} = {value!r}")


def cmd_unset(args) -> int:
    ws = resolve_workspace(args.workspace)
    data = _load_for_write(ws)
    if _del_nested(data, args.key.split(".")):
        return _finish(ws, data, f"已删除 {args.key}")
    print(f"[FAIL] {args.key} 不存在")
    return 1


def cmd_show(args) -> int:
    ws = resolve_workspace(args.workspace)
    cfg = load_engine_config(ws)
    engine = resolve_engine()
    print("=" * 78)
    print("生效配置（层级回执：config.py 默认 < config.local.json < 环境变量）")
    print("=" * 78)
    print(f"引擎       : {engine}")
    print(f"工作区     : {ws}")
    print(f"覆盖层     : {ws / 'config.local.json'}")
    print(f"API Key    : {mask_key(cfg.RUNNINGHUB_API_KEY)}"
          + ("  [来源: 环境变量]" if __import__('os').getenv("RUNNINGHUB_API_KEY")
             else "  [来源: 覆盖层或未设置]"))
    print(f"RH 入口    : {cfg.RH_BASE_URL}")
    print(f"激活模型   : {cfg.ACTIVE_IMAGE_PROVIDER}")
    for name, prov in cfg.IMAGE_PROVIDERS.items():
        mark = "*" if name == cfg.ACTIVE_IMAGE_PROVIDER else " "
        print(f" {mark} image_providers.{name}: kind={prov.get('kind')} id={prov.get('id') or '(空)'}")
        for role, node in (prov.get("nodes") or {}).items():
            print(f"      nodes.{role:<11} nodeId={str(node.get('nodeId') or '(空)'):<10} "
                  f"fieldName={node.get('fieldName') or '(空)'}"
                  + ("  multi" if node.get("multi") else ""))
    print(f"h3         : kind={cfg.H3.get('kind')} id={cfg.H3.get('id') or '(空)'} "
          f"frame_source={cfg.H3.get('frame_source')}")
    for role, node in (cfg.H3.get("nodes") or {}).items():
        print(f"      nodes.{role:<11} nodeId={str(node.get('nodeId') or '(空)'):<10} "
              f"fieldName={node.get('fieldName') or '(空)'}")
    print(f"并发/批次  : concurrency={cfg.CONCURRENCY} (max {cfg.CONCURRENCY_MAX}) "
          f"batch_size={cfg.BATCH_SIZE} poll_channel={cfg.POLL_CHANNEL}")
    print(f"熔断       : 连续失败>={cfg.FUSE_CONSECUTIVE_FAILS} 或 失败率>{cfg.FUSE_FAIL_RATE:.0%} "
          f"(min_samples={cfg.FUSE_MIN_SAMPLES})")
    if cfg.OVERLAY_WARNINGS:
        print("-" * 78)
        print("[WARN] 覆盖层警告:")
        for w in cfg.OVERLAY_WARNINGS:
            print(f"  - {w}")
    miss = cfg.missing_configs()
    print("-" * 78)
    print("缺失配置:" if miss else "必填项齐全。")
    for m in miss:
        print(f"  - {m}")
    return 0 if not miss else 1


# ===========================================================================
# CLI
# ===========================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="ai-video-pipeline 配置引导与回填")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="初始化工作区")
    p.add_argument("--workspace", default=None)
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("set-workspace", help="更新 .workspace 指针")
    p.add_argument("path")
    p.set_defaults(fn=cmd_set_workspace)

    p = sub.add_parser("set-key", help="写 API Key（兜底；环境变量优先）")
    p.add_argument("--value", default=None)
    p.add_argument("--from-env", default=None, metavar="VAR")
    p.add_argument("--workspace", default=None)
    p.set_defaults(fn=cmd_set_key)

    p = sub.add_parser("set-provider", help="写图片工作流 ID")
    p.add_argument("--provider", default="banana")
    p.add_argument("--kind", choices=["workflow", "ai-app"], default="workflow")
    p.add_argument("--id", default=None)
    p.add_argument("--label", default=None)
    p.add_argument("--workspace", default=None)
    p.set_defaults(fn=cmd_set_provider)

    p = sub.add_parser("set-h3", help="写 H3 工作流 ID")
    p.add_argument("--kind", choices=["workflow", "ai-app"], default="workflow")
    p.add_argument("--id", default=None)
    p.add_argument("--frame-source", choices=["cell_01", "grid_raw"], default=None)
    p.add_argument("--workspace", default=None)
    p.set_defaults(fn=cmd_set_h3)

    p = sub.add_parser("apply-nodes", help="把 probe 候选片段回填进覆盖层")
    p.add_argument("--from", dest="src", required=True)
    p.add_argument("--target", choices=["banana", "h3"], required=True)
    p.add_argument("--yes", action="store_true", help="二次确认后才真正写入")
    p.add_argument("--workspace", default=None)
    p.set_defaults(fn=cmd_apply_nodes)

    p = sub.add_parser("set", help="按点路径写白名单键")
    p.add_argument("--key", required=True)
    p.add_argument("--value", required=True)
    p.add_argument("--workspace", default=None)
    p.set_defaults(fn=cmd_set)

    p = sub.add_parser("unset", help="删除某个键")
    p.add_argument("--key", required=True)
    p.add_argument("--workspace", default=None)
    p.set_defaults(fn=cmd_unset)

    p = sub.add_parser("show", help="打印生效配置（Key 打码）")
    p.add_argument("--workspace", default=None)
    p.set_defaults(fn=cmd_show)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
