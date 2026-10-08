# -*- coding: utf-8 -*-
"""
tools/probe_nodes.py —— 节点发现 + 候选映射生成

作用：调用 RunningHub 的节点发现接口，列出工作流 / AI 应用里**可被 API 填值的节点**，
     并可按启发式生成 config.local.json 的候选配置片段（必须经用户确认后才回填）。

用法：
    python tools/probe_nodes.py                          # 探测 config 里配置的图片模型 + H3
    python tools/probe_nodes.py --json                   # 额外打印原始 JSON
    python tools/probe_nodes.py --kind workflow --id <你的工作流id>
    python tools/probe_nodes.py --target banana --emit config-fragment
    python tools/probe_nodes.py --target h3     --emit config-fragment --write  # 谨慎：直接回填

需要先设置 API Key：  set RUNNINGHUB_API_KEY=你的key
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))          # 让本脚本能 import 到引擎目录的 config / runninghub_client

import config as C                                                    # noqa: E402
from runninghub_client import RunningHubClient, RunningHubError       # noqa: E402

# ---------------------------------------------------------------- 启发式词典
_TEXT_HINTS = {"text", "prompt", "positive", "positive_prompt", "string", "textarea", "caption"}
_RATIO_HINTS = {"aspectratio", "aspect_ratio", "ratio", "size"}
_IMAGE_HINTS = {"image", "imageurls", "imageurl", "image1", "image2", "img", "src", "file",
                "imagelist", "image_list", "refimage", "reference_image"}
_NUM_HINTS = {"duration", "seconds", "length", "frames"}


def _norm(field_name: str) -> str:
    return str(field_name or "").strip().lower().replace("-", "_").replace(" ", "_")


def guess_role(field_name: str, class_type: str = "") -> str:
    """根据 fieldName / classType 猜节点角色。"""
    n, ct = _norm(field_name), _norm(class_type)
    if any(h in n for h in _TEXT_HINTS) or "text" in ct or "prompt" in ct:
        return "text"
    if any(h in n for h in _RATIO_HINTS):
        return "ratio"
    if any(h in n for h in _NUM_HINTS) or "int" in ct or "float" in ct or "number" in ct:
        return "number"
    if any(h in n for h in _IMAGE_HINTS) or "image" in ct or "loadimage" in ct:
        return "image"
    return "unknown"


def _confidence(field_name: str, role: str) -> str:
    n = _norm(field_name)
    if role == "text" and n in ("text", "prompt", "positive"):
        return "high"
    if role == "ratio" and n in ("aspectratio", "aspect_ratio"):
        return "high"
    if role == "number" and n == "duration":
        return "high"
    if role == "image" and n in ("image", "imageurls"):
        return "high"
    if role == "unknown":
        return "low"
    return "medium"


def build_fragment(nodes: list, target: str, wf_id: str, kind: str) -> dict:
    """把探测到的节点转成 config.local.json 的候选片段（confidence 由调用方标注）。"""
    text_nodes = [n for n in nodes if guess_role(str(n.get("fieldName", "")),
                                                 str(n.get("classType") or "")) == "text"]
    ratio_nodes = [n for n in nodes if guess_role(str(n.get("fieldName", "")),
                                                  str(n.get("classType") or "")) == "ratio"]
    num_nodes = [n for n in nodes if guess_role(str(n.get("fieldName", "")),
                                                str(n.get("classType") or "")) == "number"]
    image_nodes = [n for n in nodes if guess_role(str(n.get("fieldName", "")),
                                                  str(n.get("classType") or "")) == "image"]

    def node_of(n, multi=False):
        if not n:
            return {"nodeId": "", "fieldName": ""}
        d = {"nodeId": str(n.get("nodeId", "")), "fieldName": str(n.get("fieldName", ""))}
        if multi:
            d["multi"] = True
        return d

    frag: dict = {"kind": kind, "id": wf_id}
    if target == "banana":
        frag["nodes"] = {
            "prompt": node_of(text_nodes[0] if text_nodes else None),
            "ratio": node_of(ratio_nodes[0] if ratio_nodes else None),
        }
        if len(image_nodes) >= 2:
            frag["nodes"]["char_ref"] = node_of(image_nodes[0], multi=True)
            frag["nodes"]["scene_ref"] = node_of(image_nodes[1], multi=True)
        elif len(image_nodes) == 1:
            frag["nodes"]["image"] = node_of(image_nodes[0], multi=True)
        else:
            frag["nodes"]["char_ref"] = {"nodeId": "", "fieldName": "", "multi": True}
            frag["nodes"]["scene_ref"] = {"nodeId": "", "fieldName": "", "multi": True}
    else:  # h3
        frag["frame_source"] = C.H3.get("frame_source", "cell_01")
        frag["nodes"] = {
            "prompt": node_of(text_nodes[0] if text_nodes else None),
            "first_frame": node_of(image_nodes[0] if image_nodes else None),
            "char_ref": node_of(image_nodes[1] if len(image_nodes) > 1 else None),
            "scene_ref": node_of(image_nodes[2] if len(image_nodes) > 2 else None),
            "grid_ref": node_of(image_nodes[3] if len(image_nodes) > 3 else None),
            "duration": node_of(num_nodes[0] if num_nodes else None),
            "ratio": node_of(ratio_nodes[0] if ratio_nodes else None),
        }
    return frag


def annotate_confidence(nodes: list, frag: dict) -> dict:
    """给候选片段每个节点项标注 confidence：high / medium / low。"""
    by_key = {}
    for n in nodes:
        by_key[(str(n.get("nodeId", "")), str(n.get("fieldName", "")))] = n
    nodes_cfg = frag.get("nodes") or {}
    for role, node in nodes_cfg.items():
        if not isinstance(node, dict) or not node.get("nodeId"):
            if isinstance(node, dict):
                node["confidence"] = "none"
            continue
        src = by_key.get((str(node.get("nodeId")), str(node.get("fieldName"))))
        role_guess = guess_role(str(node.get("fieldName", "")),
                                str((src or {}).get("classType") or ""))
        node["confidence"] = _confidence(str(node.get("fieldName", "")), role_guess)
    return frag


def _print_nodes(title: str, kind: str, target_id, nodes: list) -> None:
    print("\n" + "=" * 80)
    print(f"{title}\n  kind={kind}   id={target_id}   可填节点数={len(nodes)}")
    print("=" * 80)
    if not nodes:
        print("  ! 没拿到可编辑输入节点。可能原因：")
        print("    - 工作流没有暴露 API 输入（需要在 RunningHub 工作流里设置 API 节点）")
        print("    - ID 填错，或该工作流不属于当前 API Key 的账号")
        return
    print(f"  {'nodeId':<12}{'fieldName':<26}{'猜测角色':<10}{'classType / 说明':<26}示例值")
    print("  " + "-" * 78)
    for n in nodes:
        nid = str(n.get("nodeId", ""))
        fn = str(n.get("fieldName", ""))
        role = guess_role(fn, str(n.get("classType") or ""))
        extra = str(n.get("classType") or n.get("description") or "")[:24]
        val = n.get("fieldValue")
        val_s = "" if val in (None, "") else str(val)
        if len(val_s) > 36:
            val_s = val_s[:33] + "..."
        print(f"  {nid:<12}{fn:<26}{role:<10}{extra:<26}{val_s}")


def _resolve_workspace(explicit: str | None) -> Path:
    for cand in (explicit, os.getenv("VIDEO_PIPELINE_WORKSPACE")):
        if cand and str(cand).strip():
            return Path(str(cand)).expanduser().resolve()
    ptr = ROOT / ".workspace"
    if ptr.is_file():
        line = ptr.read_text(encoding="utf-8-sig").strip()
        if line:
            return Path(line).expanduser().resolve()
    return (Path.home() / "video-pipeline-workspace").resolve()


async def probe_and_maybe_emit(args) -> int:
    ws = _resolve_workspace(args.workspace)
    C.apply_overlay(ws / C.OVERLAY_FILENAME)

    if args.target == "banana":
        prov = C.get_active_provider()
        kind, wf_id = prov.get("kind", "workflow"), prov.get("id")
        title = f"图片模型【{C.ACTIVE_IMAGE_PROVIDER}】{prov.get('label', '')}"
    else:
        kind, wf_id = C.H3.get("kind", "workflow"), C.H3.get("id")
        title = "H3 视频工作流"

    if not wf_id:
        print(f"! {title} 的 id 未填写（先 configure.py set-provider / set-h3）")
        return 2

    async with RunningHubClient(C.RUNNINGHUB_API_KEY, C.RH_BASE_URL, C.HTTP_TIMEOUT,
                                channel=C.POLL_CHANNEL) as client:
        try:
            nodes = await client.get_nodes(kind, wf_id)
        except RunningHubError as e:
            print(f"! 探测失败（{title}）：{e}")
            return 1

    _print_nodes(title, kind, wf_id, nodes)
    if args.json:
        print("\n  --- 原始节点 ---")
        print(json.dumps(nodes, ensure_ascii=False, indent=2)[:4000])
    if not nodes:
        return 1

    frag = annotate_confidence(nodes, build_fragment(nodes, args.target, wf_id, kind))
    payload = {
        "target": args.target,
        "kind": kind,
        "id": wf_id,
        "workspace": str(ws),
        "candidates": {args.target: frag},
        "note": "候选映射由启发式生成，low 一律需要人工确认；回填前必须逐项核对",
    }
    print("\n" + "-" * 80)
    print("候选配置片段（请逐项确认，尤其 confidence=low/medium 的项）：")
    print(json.dumps({args.target: frag}, ensure_ascii=False, indent=2))

    out_path = None
    if args.emit:
        out_path = Path(args.emit).expanduser()
        if not out_path.is_absolute():
            out_path = ws / out_path
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print(f"\n[OK] 候选片段已写入：{out_path}")
    # --write 已从命令行移除（回填一律走 configure.py apply-nodes）。
    # 这里用 getattr 兜底，否则 argparse 里已不存在的 args.write 会让整个 probe
    # 在写完文件后抛 AttributeError 并以 exit 1 收场 —— 看起来像探测失败，实则成功。
    if getattr(args, "write", False):
        if not out_path:
            print("! --write 需要同时给 --emit <文件>")
            return 2
        print("[SKIP] --write 已停用：回填一律走 configure.py apply-nodes（含 --yes 二次确认）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="探测 RunningHub 工作流的可填节点并生成候选配置")
    ap.add_argument("--kind", choices=["workflow", "ai-app"], default=None,
                    help="临时探测指定类型（需同时给 --id）")
    ap.add_argument("--id", dest="target_id", default=None, help="临时探测指定工作流 / 应用 ID")
    ap.add_argument("--target", choices=["banana", "h3"], default=None,
                    help="按 config 里的配置探测指定目标（banana=当前激活图片模型，h3=H3）")
    ap.add_argument("--workspace", default=None, help="工作区根目录（读取 config.local.json）")
    ap.add_argument("--emit", default=None, metavar="FILE",
                    help="把候选片段写入该文件（相对工作区）")
    ap.add_argument("--json", action="store_true", help="额外打印原始 JSON")
    args = ap.parse_args()

    if not C.RUNNINGHUB_API_KEY:
        print("! 未设置 RUNNINGHUB_API_KEY。请先执行：  set RUNNINGHUB_API_KEY=你的key")
        return 2

    if args.target:
        return asyncio.run(probe_and_maybe_emit(args))

    if not (args.target_id or args.kind):
        print("! 请给 --target banana|h3，或同时给 --kind 与 --id")
        return 2
    kind = args.kind or "workflow"
    asyncio.run(_probe_plain(kind, args.target_id, f"临时探测（{kind}）", args.json))
    return 0


async def _probe_plain(kind: str, target_id, title: str, raw: bool) -> None:
    async with RunningHubClient(C.RUNNINGHUB_API_KEY, C.RH_BASE_URL, C.HTTP_TIMEOUT,
                                channel=C.POLL_CHANNEL) as client:
        try:
            nodes = await client.get_nodes(kind, target_id)
        except RunningHubError as e:
            print(f"\n! 探测失败（{title}）：{e}")
            return
        _print_nodes(title, kind, target_id, nodes)
        if raw:
            print("\n  --- 原始节点 ---")
            print(json.dumps(nodes, ensure_ascii=False, indent=2)[:4000])


if __name__ == "__main__":
    sys.exit(main())
