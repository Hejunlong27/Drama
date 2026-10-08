#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""prepare_refs.py —— 参考图瘦身工具（流水线前置，不联网、不计费、不改原图）

背景（2026-09-18 核实）：
  data/refs/ 里的 char_01..09 是 6000x3376 / 22-28MB 的 PNG。
  MiniMax H3 官方能力边界：图片宽高范围 256-5760px，多模态参考图片单张 <= 30MB。
  -> 6000px 已越界；且单段最多 5 张 x 25MB ~= 125MB 上传量/段。

本脚本做三件事（默认只报告，写入必须显式 --apply）：
  1) 等比缩放到「长边 <= --max-edge」（默认 2048），只缩不放；
  2) 转成 JPEG（质量 --quality，默认 92）或保留 PNG（--format png）；
  3) 输出到 --out-dir（默认 data/refs_h3），**原图与 characters.json 一律不动**。

把流水线指到新目录需要另外一步（本脚本 --write-config 才会做，且会先备份原文件）：
  data/characters.json 的 ./refs/xxx.png  ->  ./refs_h3/xxx.jpg

用法：
  python prepare_refs.py                          # 只体检，打印对照表
  python prepare_refs.py --apply                  # 生成 data/refs_h3/
  python prepare_refs.py --apply --write-config   # 再把 characters.json 指过去（自动备份）

退出码：0 正常 / 1 参数或环境错误 / 2 体检发现越界图但未 --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    print("[FAIL] 缺少 Pillow。请先安装：pip install Pillow")
    sys.exit(1)

# --- H3 官方硬约束（用于体检告警）-----------------------------------------
H3_MAX_EDGE = 5760          # 图片宽高范围上限（像素）
H3_MAX_BYTES = 30 * 1024 * 1024  # 单张 <= 30MB

DEFAULT_MAX_EDGE = 2048     # 输出长边；远高于任何视频模型需要的参考精度
DEFAULT_QUALITY = 92


def human(n: int) -> str:
    return f"{n / 1024 / 1024:.2f} MB"


def collect_sources(workspace: Path) -> list[Path]:
    """从 characters.json / scenes.json 里读引用，读不到就退回扫 refs/ 目录。"""
    found: list[Path] = []
    seen: set[str] = set()
    for name in ("characters.json", "scenes.json"):
        f = workspace / "data" / name
        if not f.is_file():
            continue
        try:
            table = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[WARN] 读不了 {f.name}: {exc}")
            continue
        for key, raw in table.items():
            if key.startswith("_") or not isinstance(raw, str):
                continue
            if raw.lower().startswith(("http://", "https://", "data:")):
                continue
            p = Path(raw)
            if not p.is_absolute():
                p = workspace / "data" / p
            p = p.resolve()
            if str(p) in seen:
                continue
            seen.add(str(p))
            found.append(p)

    if not found:
        refs = workspace / "data" / "refs"
        if refs.is_dir():
            found = sorted(p for p in refs.iterdir() if p.is_file())
            if found:
                print(f"[WARN] 两张表里没有本地引用，改为扫描 {refs}")
    return found


def inspect(src: Path) -> dict:
    size = src.stat().st_size
    with Image.open(src) as im:
        w, h = im.size
        mode = im.mode
    return {
        "path": src,
        "w": w, "h": h, "mode": mode, "bytes": size,
        "over_edge": max(w, h) > H3_MAX_EDGE,
        "over_size": size > H3_MAX_BYTES,
    }


def target_size(w: int, h: int, max_edge: int) -> tuple[int, int]:
    """等比缩到长边 <= max_edge；只缩不放。"""
    longest = max(w, h)
    if longest <= max_edge:
        return w, h
    k = max_edge / longest
    # 取偶数，规避部分编码器对奇数边的处理差异
    nw = max(2, int(round(w * k)) // 2 * 2)
    nh = max(2, int(round(h * k)) // 2 * 2)
    return nw, nh


def main() -> int:
    ap = argparse.ArgumentParser(description="参考图瘦身（默认只报告）")
    ap.add_argument("--workspace", default=None, help="工作区；默认脚本所在目录的 video-pipeline/")
    ap.add_argument("--out-dir", default=None, help="输出目录；默认 <工作区>/data/refs_h3")
    ap.add_argument("--max-edge", type=int, default=DEFAULT_MAX_EDGE, help=f"长边上限（默认 {DEFAULT_MAX_EDGE}）")
    ap.add_argument("--quality", type=int, default=DEFAULT_QUALITY, help=f"JPEG 质量（默认 {DEFAULT_QUALITY}）")
    ap.add_argument("--format", choices=("jpg", "png"), default="jpg", help="输出格式（默认 jpg）")
    ap.add_argument("--apply", action="store_true", help="真的写文件；不给则只体检")
    ap.add_argument("--write-config", action="store_true", help="顺带把 characters.json 指到新目录（先备份）")
    args = ap.parse_args()

    if args.workspace:
        workspace = Path(args.workspace).resolve()
    else:
        workspace = (Path(__file__).resolve().parent.parent / "video-pipeline").resolve()

    if not (workspace / "data").is_dir():
        print(f"[FAIL] 找不到工作区 data/：{workspace}")
        return 1

    out_dir = Path(args.out_dir).resolve() if args.out_dir else workspace / "data" / "refs_h3"
    ext = ".jpg" if args.format == "jpg" else ".png"

    sources = collect_sources(workspace)
    if not sources:
        print("[FAIL] 没有找到任何本地参考图")
        return 1

    print(f"工作区   : {workspace}")
    print(f"输出目录 : {out_dir}")
    print(f"目标     : 长边 <= {args.max_edge}, 格式 {args.format}"
          f"{f', 质量 {args.quality}' if args.format == 'jpg' else ''}")
    print("-" * 78)
    print(f"{'文件':<14}{'原尺寸':>12}{'原体积':>12}{'新尺寸':>12}{'新体积':>12}  备注")
    print("-" * 78)

    rows: list[dict] = []
    bad = 0
    for src in sources:
        try:
            info = inspect(src)
        except OSError as exc:
            print(f"{src.name:<14}{'--':>12}{'--':>12}{'--':>12}{'--':>12}  [FAIL] 打不开: {exc}")
            bad += 1
            continue

        nw, nh = target_size(info["w"], info["h"], args.max_edge)
        dst = out_dir / (src.stem + ext)

        notes = []
        if info["over_edge"]:
            notes.append(f"超 H3 {H3_MAX_EDGE}px")
        if info["over_size"]:
            notes.append("超 H3 30MB")

        new_bytes = 0
        if args.apply:
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                with Image.open(src) as im:
                    im = im.convert("RGB")
                    if (nw, nh) != im.size:
                        im = im.resize((nw, nh), Image.LANCZOS)
                    if args.format == "jpg":
                        im.save(dst, "JPEG", quality=args.quality, optimize=True, progressive=True)
                    else:
                        im.save(dst, "PNG", optimize=True)
                new_bytes = dst.stat().st_size
            except OSError as exc:
                print(f"{src.name:<14}{'':>12}{'':>12}{'':>12}{'':>12}  [FAIL] 写入失败: {exc}")
                bad += 1
                continue

        old_dim = f"{info['w']}x{info['h']}"
        new_dim = f"{nw}x{nh}"
        old_size = human(info["bytes"])
        new_size = human(new_bytes) if new_bytes else "-"
        note = "; ".join(notes) or "OK"
        if new_bytes:
            note += f"  [{info['bytes'] / new_bytes:.1f}x]"
        print(f"{src.name:<14}{old_dim:>12}{old_size:>12}{new_dim:>12}{new_size:>12}  {note}")

        rows.append({**info, "nw": nw, "nh": nh, "dst": str(dst), "new_bytes": new_bytes})

    print("-" * 78)
    over = [r for r in rows if r["over_edge"]]
    total_old = sum(r["bytes"] for r in rows)
    total_new = sum(r["new_bytes"] for r in rows)
    print(f"共 {len(rows)} 张；越界(>{H3_MAX_EDGE}px) {len(over)} 张；失败 {bad} 张")
    if args.apply:
        print(f"总体积 {human(total_old)} -> {human(total_new)}"
              f"（省 {100 * (1 - total_new / total_old):.1f}%）")
    else:
        print("这是体检模式，没有写任何文件。加 --apply 才会生成。")
        if over:
            print(f"[WARN] 有 {len(over)} 张超过 H3 官方 {H3_MAX_EDGE}px 上限，建议尽快 --apply。")

    if args.write_config and args.apply:
        cfg = workspace / "data" / "characters.json"
        if not cfg.is_file():
            print(f"[WARN] 没有 {cfg}，跳过改配置")
        else:
            backup = cfg.with_suffix(".json.bak")
            shutil.copy2(cfg, backup)
            table = json.loads(cfg.read_text(encoding="utf-8"))
            changed = 0
            for key, raw in list(table.items()):
                if key.startswith("_") or not isinstance(raw, str):
                    continue
                if raw.lower().startswith(("http://", "https://", "data:")):
                    continue
                # 任何指向本地图片的项，一律改指到 refs_h3/<同名><新后缀>
                table[key] = f"./refs_h3/{Path(raw).stem}{ext}"
                changed += 1
            cfg.write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[OK] characters.json 已指向 refs_h3（改 {changed} 项），备份：{backup.name}")
    elif args.write_config and not args.apply:
        print("[WARN] --write-config 需要同时加 --apply，已忽略。")

    if bad:
        return 1
    if over and not args.apply:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
