# -*- coding: utf-8 -*-
"""
report.py —— 交付汇报器（G6 关卡用，不联网）

读 <工作区>/output/<batch_id>/summary.json + state.json，输出固定四段式摘要：
  ① 战报一句  ② 花费  ③ 失败清单 + 重跑命令  ④ 产物目录

用法：
    scripts/report.py --batch 20250917_153000
    scripts/report.py --batch 20250917_153000 --json
    scripts/report.py --latest          # 自动取 output/ 下最新的批次
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_layer import force_utf8_stdio, resolve_workspace       # noqa: E402

force_utf8_stdio()


def _find_batch(out_root: Path, batch: str | None) -> Path:
    if batch:
        p = out_root / batch
        if not p.is_dir():
            raise SystemExit(f"[FAIL] 找不到批次目录: {p}")
        return p
    cands = sorted([d for d in out_root.iterdir() if d.is_dir()
                    and (d / "summary.json").is_file()],
                   key=lambda d: d.stat().st_mtime)
    if not cands:
        raise SystemExit(f"[FAIL] {out_root} 下没有任何含 summary.json 的批次")
    return cands[-1]


def _rerun_cmd(batch_id: str, failed_ids: List[str]) -> str:
    if not failed_ids:
        return ""
    if len(failed_ids) <= 3:
        return f"run.py --only {','.join(failed_ids)} --resume {batch_id}"
    return f"run.py --resume {batch_id}"


def build_report(batch_dir: Path) -> Dict[str, Any]:
    summary = json.loads((batch_dir / "summary.json").read_text(encoding="utf-8"))
    totals = summary.get("totals") or {}
    usage = summary.get("usage") or {}
    fuse = summary.get("fuse") or {}

    failed: List[Dict[str, Any]] = []
    for row in summary.get("shots") or []:
        stage = None
        if row.get("image_status") == "FAILED":
            stage = "image"
        elif row.get("crop_status") == "FAILED":
            stage = "crop"
        elif row.get("video_status") == "FAILED":
            stage = "video"
        if stage or row.get("error"):
            failed.append({"shot_id": row.get("shot_id"), "stage": stage,
                           "error": (row.get("error") or "")[:200]})
    failed_ids = [f["shot_id"] for f in failed if f.get("shot_id")]

    mp4s = sorted(str(p) for p in batch_dir.glob("*/final.mp4"))

    return {
        "batch_id": summary.get("batch_id") or batch_dir.name,
        "batch_dir": str(batch_dir),
        "dry_run": summary.get("dry_run"),
        "resumed": summary.get("resumed"),
        "elapsed_seconds": summary.get("elapsed_seconds"),
        "totals": totals,
        "usage": usage,
        "fuse": fuse,
        "config": summary.get("config"),
        "failed": failed,
        "rerun_command": _rerun_cmd(summary.get("batch_id") or batch_dir.name, failed_ids),
        "mp4_count": len(mp4s),
        "mp4_sample": mp4s[:3],
        "artifact_dir": str(batch_dir),
        "log_file": str(batch_dir / "run.log") if (batch_dir / "run.log").is_file() else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="读取 summary.json 生成交付汇报")
    ap.add_argument("--batch", default=None, help="批次 ID 或目录名")
    ap.add_argument("--latest", action="store_true", help="取 output/ 下最新批次")
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    ws = resolve_workspace(args.workspace)
    out_root = ws / "output"
    if not out_root.is_dir():
        print(f"[FAIL] 工作区没有 output 目录: {ws}")
        return 1
    batch_dir = _find_batch(out_root, args.batch if not args.latest else None)
    rep = build_report(batch_dir)

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    t = rep["totals"]
    print("=" * 78)
    print(f"① 战报：成功 {t.get('success', 0)} / 失败 {t.get('failed', 0)} "
          f"/ 跳过 {t.get('skipped', 0)} / 未完成 {t.get('pending', 0)}"
          f"（共 {t.get('total', 0)}）"
          + ("  [DRY-RUN 预演]" if rep.get("dry_run") else "")
          + ("  [续跑]" if rep.get("resumed") else ""))
    print(f"   耗时 {rep.get('elapsed_seconds')}s | 批次 {rep['batch_id']}")
    print("-" * 78)
    u = rep["usage"]
    print(f"② 花费：{u.get('consumeMoney', 0)} 元 / {u.get('consumeCoins', 0)} 分"
          f"（RunningHub 实际计费为准）")
    if rep.get("fuse", {}).get("blown"):
        print(f"   [WARN] 已熔断：{rep['fuse'].get('reason')}")
    print("-" * 78)
    if rep["failed"]:
        print(f"③ 失败清单（{len(rep['failed'])} 个）：")
        for f in rep["failed"][:12]:
            print(f"   - {f['shot_id']}  阶段={f['stage'] or '?'}  {f['error'][:120]}")
        if len(rep["failed"]) > 12:
            print(f"   ... 其余 {len(rep['failed']) - 12} 个见 summary.json")
        print(f"   重跑: {rep['rerun_command']}")
    else:
        print("③ 失败清单：无")
    print("-" * 78)
    print(f"④ 产物：{rep['mp4_count']} 个 mp4 -> {rep['artifact_dir']}")
    for p in rep["mp4_sample"]:
        print(f"   {p}")
    if rep.get("log_file"):
        print(f"   日志: {rep['log_file']}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
