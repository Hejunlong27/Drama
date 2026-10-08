#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
upscale_compare_frames.py —— 把「同一个镜头的不同放大版本」拼成一张对比图，用眼睛定夺

为什么需要它
------------
放大模型的差别**不在全局指标上**（实测 prob-4 与 iris-3 的高频/拉普拉斯增益几乎一样），
而在"脸有没有变塑料""暗部有没有糊成一片"这种**审美**层面 —— 指标答不了，只能看图。
本机没有 vision provider，所以脚本负责**把图摆好**（同一帧、同一裁切区域、标注清楚），
人只需要看一眼。

产出（默认每帧两张）：
    cmp_full_t15s.png    四宫格全景：原始 / 纯插值 / 模型A / 模型B（各缩放到 1280x740）
    cmp_zoom_t15s.png    1:1 像素细节：纯插值 / 模型A / 模型B 三行叠放（同一裁切位置）
两图都带中文标注（用系统 ffmpeg 的 drawtext；Topaz 自带的那个 ffmpeg 没编译 drawtext）。

用法
----
    python scripts/upscale_compare_frames.py ^
        --ref  video-pipeline/output/subs/ep04/ep04_master.mp4 ^
        --a video-pipeline/output/_bench/ep04_prob4_60s.mp4 --label-a prob-4 ^
        --b video-pipeline/output/_bench/ep04_iris3_60s.mp4 --label-b iris-3 ^
        --t 15 40 --outdir video-pipeline/output/_bench/compare
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


def harden_stdio() -> None:
    try:
        enc = "utf-8"
        if sys.stdout.isatty() and os.name == "nt":
            import ctypes
            cp = ctypes.windll.kernel32.GetConsoleOutputCP()
            if cp:
                enc = "cp%d" % cp
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(encoding=enc, errors="replace")   # type: ignore[union-attr]
            except Exception:                                    # noqa: BLE001
                pass
    except Exception:                                            # noqa: BLE001
        pass


def find_ffmpeg() -> str:
    """优先系统 ffmpeg（要有 drawtext 才能标注），退化到 Topaz 自带那个。"""
    import shutil
    cands = [shutil.which("ffmpeg")]
    cands += [p for p in (os.environ.get("FFMPEG_PATH"),
                          os.environ.get("TOPAZ_FFMPEG_PATH")) if p]
    for c in cands:
        if not c or not os.path.exists(c):
            continue
        p = subprocess.run([c, "-hide_banner", "-filters"], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
        if b"drawtext" in p.stdout:
            return c
    return shutil.which("ffmpeg") or (cands[-1] or "ffmpeg")


def probe_size(ffmpeg_dir_ffprobe: str, path: str):
    p = subprocess.run([ffmpeg_dir_ffprobe, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    nums = p.stdout.decode("utf-8", "replace").replace("\n", ",").split(",")
    nums = [n for n in nums if n.strip().isdigit()]
    return int(nums[0]), int(nums[1])


def label_filter(text: str, font: str) -> str:
    esc = text.replace(":", "\\:").replace("'", "\\'")
    f = font.replace("\\", "/").replace(":", "\\:")
    return ("drawtext=fontfile='%s':text='%s':x=16:y=12:fontsize=42:"
            "fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=12" % (f, esc))


def main() -> int:
    harden_stdio()
    ap = argparse.ArgumentParser(description="拼放大模型对比图（全景四宫格 + 1:1 细节）")
    ap.add_argument("--ref", required=True, help="原始母版（1440p，作为参考）")
    ap.add_argument("--a", required=True, help="模型 A 的输出（2K）")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--b", default=None, help="模型 B 的输出（2K，可选）")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--t", type=float, nargs="+", default=[15.0], help="取帧时间点（秒）")
    ap.add_argument("--outdir", default="video-pipeline/output/_bench/compare")
    ap.add_argument("--zoom-h", type=int, default=370, help="1:1 细节条的高度（像素）")
    ap.add_argument("--zoom-y", type=int, default=300, help="1:1 细节条的 y 起点")
    args = ap.parse_args()

    ff = find_ffmpeg()
    ffprobe = os.path.join(os.path.dirname(ff), "ffprobe.exe")
    if not os.path.exists(ffprobe):
        import shutil
        ffprobe = shutil.which("ffprobe") or "ffprobe"
    W, H = probe_size(ffprobe, args.a)
    tiles = [("原始 %dx%d" % tuple(probe_size(ffprobe, args.ref)), args.ref, "lanczos"),
             ("纯插值 lanczos", args.ref, "lanczos"),
             (args.label_a, args.a, "copy")]
    if args.b:
        tiles.append((args.label_b, args.b, "copy"))
    os.makedirs(args.outdir, exist_ok=True)
    font = os.environ.get("SUBTITLE_FONT_PATH") or r"C:/Windows/Fonts/msyh.ttc"

    zoom_w = min(1280, W)
    zoom_y = max(0, min(args.zoom_y, H - args.zoom_h))
    made = []
    for t in args.t:
        # ---------- 全景四宫格：每格缩放到 1280x740 ----------
        tw, th = W // 2, H // 2
        inputs, chains, labels = [], [], []
        for i, (name, path, mode) in enumerate(tiles):
            inputs += ["-ss", "%.3f" % t, "-i", path]
            pre = "scale=%d:%d:flags=lanczos," % (tw, th)
            chain = "[%d:v]%s%s%s[v%d]" % (i, pre, label_filter(name, font), "", i)
            # drawtext 放在 scale 之后：字号才与最终画面成比例
            chain = "[%d:v]scale=%d:%d:flags=lanczos,%s[v%d]" % (
                i, tw, th, label_filter(name, font), i)
            chains.append(chain)
            labels.append("[v%d]" % i)
        n = len(tiles)
        layout = "0_0|%d_0|0_%d|%d_%d" % (tw, th, tw, th) if n == 4 else "0_0|0_%d" % th
        out = os.path.join(args.outdir, "cmp_full_t%gs.png" % t)
        fc = ";".join(chains) + ";%sxstack=inputs=%d:layout=%s[o]" % (
            "".join(labels), n, layout)
        cmd = [ff, "-hide_banner", "-v", "error", "-y"] + inputs + [
            "-filter_complex", fc, "-map", "[o]", "-frames:v", "1", out]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if os.path.exists(out):
            made.append(out)
        else:
            print("[FAIL] 全景图失败：%s" % r.stdout.decode("utf-8", "replace")[-400:])

        # ---------- 1:1 细节：同一裁切位置，三/四行叠放 ----------
        inputs, chains, labels = [], [], []
        for i, (name, path, mode) in enumerate(tiles):
            inputs += ["-ss", "%.3f" % t, "-i", path]
            # ★ 都先统一到 A 的尺寸再裁同一块 —— 保证比的是"同一处像素"
            chains.append("[%d:v]scale=%d:%d:flags=lanczos,crop=%d:%d:0:%d,%s[v%d]" % (
                i, W, H, zoom_w, args.zoom_h, zoom_y, label_filter(name, font), i))
            labels.append("[v%d]" % i)
        out = os.path.join(args.outdir, "cmp_zoom_t%gs.png" % t)
        fc = ";".join(chains) + ";%svstack=inputs=%d[o]" % ("".join(labels), len(tiles))
        cmd = [ff, "-hide_banner", "-v", "error", "-y"] + inputs + [
            "-filter_complex", fc, "-map", "[o]", "-frames:v", "1", out]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if os.path.exists(out):
            made.append(out)
        else:
            print("[FAIL] 细节图失败：%s" % r.stdout.decode("utf-8", "replace")[-400:])

    print("生成 %d 张对比图（用系统 ffmpeg: %s）：" % (len(made), ff))
    for m in made:
        print("   %s  %.1f MB" % (m, os.path.getsize(m) / 1048576.0))
    order = " / ".join(t[0] for t in tiles)
    print("看图顺序（全景四宫格与 1:1 细节图**都是这个顺序**，四宫格按 左上→右上→左下→右下）：")
    print("   %s" % order)
    print("   （1:1 细节图从上到下同序，每格 %dpx 高、100%% 像素、同一裁切位置）" % args.zoom_h)
    return 0 if made else 2


if __name__ == "__main__":
    sys.exit(main())
