# -*- coding: utf-8 -*-
"""
check_billing.py —— 计费预估（G3 关卡用，不发起任何任务）

根据 state.json / 产物存在性，精确列出「将真实提交 / 将跳过」的分镜名单与调用次数。

用法：
    python run.py 或 scripts/check_billing.py
    scripts/check_billing.py --resume 20250917_153000
    scripts/check_billing.py --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_layer import (          # noqa: E402
    force_utf8_stdio, resolve_workspace, resolve_engine, load_engine_config,
)

force_utf8_stdio()


def _plan_shot(state_entry: Dict[str, Any], batch_dir: Path, sid: str,
               dry_run: bool = False) -> Dict[str, Any]:
    """判定单个 shot 将执行哪些阶段。"""
    img = state_entry.get("image_status")
    vid = state_entry.get("video_status")
    raw_ok = (batch_dir / sid / "banana_raw.png").is_file() or \
             (batch_dir / sid / "banana_raw_extracted.png").is_file()
    cropped = [p for p in (batch_dir / sid / "cropped").glob("*.png")] \
        if (batch_dir / sid / "cropped").is_dir() else []
    video_ok = vid == "SUCCESS" and (batch_dir / sid / "final.mp4").is_file()
    reused_img_task = bool(state_entry.get("image_task_id")) and img not in ("SUCCESS", "FAILED")
    reused_vid_task = bool(state_entry.get("video_task_id")) and vid not in ("SUCCESS", "FAILED")

    return {
        "shot_id": sid,
        "image_submit": not (img == "SUCCESS" and raw_ok),
        "image_reuse_task": reused_img_task,
        "video_submit": not video_ok,
        "video_reuse_task": reused_vid_task,
        "skipped": img == "SUCCESS" and raw_ok and video_ok,
    }


def build_plan(workspace: Path, resume: str | None, only: str | None,
               limit: int | None, provider_label: str, h3_id: str) -> Dict[str, Any]:
    engine = resolve_engine()
    sys.path.insert(0, str(engine))
    import main as engine_main              # noqa: E402

    data_dir = engine_main._resolve_against(workspace, engine_main.C.DATA_DIR)
    out_root = engine_main._resolve_against(workspace, engine_main.C.OUTPUT_DIR)

    inputs = engine_main.Inputs(data_dir)
    shots = inputs.shots
    if only:
        want = {s.strip() for s in only.split(",") if s.strip()}
        shots = [s for s in shots if str(s.get("shot_id")) in want]
    if limit:
        shots = shots[:limit]

    batch_dir = out_root / resume if resume else None
    state_shots: Dict[str, Any] = {}
    if resume and batch_dir and (batch_dir / "state.json").is_file():
        loaded = json.loads((batch_dir / "state.json").read_text(encoding="utf-8"))
        state_shots = loaded.get("shots") or {}

    rows = []
    for s in shots:
        sid = str(s.get("shot_id") or "?")
        rows.append(_plan_shot(state_shots.get(sid) or {}, batch_dir or out_root, sid))

    img_new = [r["shot_id"] for r in rows if r["image_submit"] and not r["image_reuse_task"]]
    vid_new = [r["shot_id"] for r in rows if r["video_submit"] and not r["video_reuse_task"]]
    img_reuse = [r["shot_id"] for r in rows if r["image_submit"] and r["image_reuse_task"]]
    vid_reuse = [r["shot_id"] for r in rows if r["video_submit"] and r["video_reuse_task"]]
    skipped = [r["shot_id"] for r in rows if r["skipped"]]

    return {
        "workspace": str(workspace),
        "batch_dir": str(batch_dir) if batch_dir else None,
        "resumed": bool(resume),
        "image_provider": provider_label,
        "h3_workflow_id": h3_id,
        "shot_total": len(shots),
        "will_submit": {
            "image_new_tasks": img_new,
            "image_reused_tasks": img_reuse,
            "video_new_tasks": vid_new,
            "video_reused_tasks": vid_reuse,
        },
        "counts": {
            "image_new": len(img_new), "image_reused": len(img_reuse),
            "video_new": len(vid_new), "video_reused": len(vid_reuse),
            "skipped": len(skipped),
            "total_calls": len(img_new) + len(vid_new),
        },
        "skipped": skipped,
        "note": "total_calls = 新建任务数；复用 taskId 只轮询不再计费。"
                "单任务实际花费以 RunningHub 计费为准，这里只是次数预估。",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="计费预估：列出将真实提交 / 将跳过的分镜")
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--resume", default=None, metavar="BATCH_ID")
    ap.add_argument("--only", default=None, help="逗号分隔的 shot_id")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    ws = resolve_workspace(args.workspace)
    cfg = load_engine_config(ws)

    if not args.resume:
        print("[WARN] 未指定 --resume：按「全新批次」估算，所有分镜都会重新提交（重复扣费风险）！")
    plan = build_plan(ws, args.resume, args.only, args.limit,
                      cfg.ACTIVE_IMAGE_PROVIDER, cfg.H3.get("id") or "(未填写)")

    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    c = plan["counts"]
    print("=" * 78)
    print("计费预估（G3 关卡，未发起任何任务）")
    print("=" * 78)
    print(f"工作区     : {plan['workspace']}")
    print(f"批次       : {plan['batch_dir'] or '（新批次）'}")
    print(f"分镜数     : {plan['shot_total']}")
    print(f"将新建任务 : 图片 {c['image_new']} 个 + 视频 {c['video_new']} 个 = {c['total_calls']} 次调用")
    if c["image_reused"] or c["video_reused"]:
        print(f"将复用任务 : 图片 {c['image_reused']} 个 + 视频 {c['video_reused']} 个（只轮询，不计费）")
    print(f"将跳过     : {c['skipped']} 个"
          + (f" -> {', '.join(plan['skipped'][:8])}{'...' if c['skipped'] > 8 else ''}"
             if plan["skipped"] else ""))
    print("-" * 78)
    if plan["counts"]["total_calls"]:
        print("确认后才允许进入 G4/G5 执行。")
    else:
        print("没有需要新建的任务，直接 --resume 即可。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
