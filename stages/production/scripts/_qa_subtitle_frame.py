# -*- coding: utf-8 -*-
"""_qa_subtitle_frame.py —— 不用人眼，用像素测字幕的【位置 / 字号 / 行数 / 是否越界】

为什么需要：本机无 vision provider，`verify_render()` 只能证明「字幕画出来了」，
**证明不了「画在哪、多大、几行、有没有越界」**。这里给出可复核的客观数字替代看图。

方法（关键）：把「母版帧 vs 烧录帧」的差异**在同一字幕内取多帧求交集**。
   单帧差异会被高运动画面污染（实测有 3 帧包围盒铺满全画面 = 89122~92058 差异像素）；
   而字幕在同一 cue 内**文本与位置都不变**，运动像素则逐帧变化
   ⇒ 多帧差异的**交集**几乎只剩字幕像素。

用法：
    python scripts\\_qa_subtitle_frame.py ep04 ep05 ep06 [--n 3]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from PIL import Image, ImageChops

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # noqa: E402
from _ws import WS as _WS_ROOT  # noqa: E402  ★ 工作区解析（env DRAMA_WORKSPACE > 位置推断 > 报错）
WS = str(_WS_ROOT)
SUBS = os.path.join(WS, "video-pipeline", "output", "subs")
SAMPLES = 5          # 每条字幕取几帧求交集
THRESH = 40          # 差异阈值，滤掉重编码噪声
BAND_RATIO = 220 / 832.0   # 底部度量带占画面高度的比例（原来写死 220 是按 1440x832 定的）


def _dims(video):
    """取视频真实分辨率 —— 高清放大后母版是 2560x1480，不能再拿配置里的 1440x832 当画布。"""
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0", video],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    nums = re.findall(r"\d+", p.stdout.decode("utf-8", errors="replace"))
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
    return None


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, p.stdout.decode("utf-8", errors="replace")


def frame_at(video, t, dst):
    if os.path.exists(dst):
        os.remove(dst)
    run(["ffmpeg", "-hide_banner", "-nostats", "-v", "error", "-y",
         "-ss", "%.3f" % t, "-i", video, "-frames:v", "1", dst])
    return os.path.exists(dst)


def cue_mask(master, subbed, c, tmpdir, tag):
    """同一 cue 内多帧差异求交集 → 返回 (mask, 用了几帧)。"""
    dur = c["end"] - c["start"]
    acc = None
    used = 0
    for k in range(SAMPLES):
        t = c["start"] + dur * (k + 0.5) / SAMPLES
        a = os.path.join(tmpdir, "_qa_a_%s.png" % tag)
        b = os.path.join(tmpdir, "_qa_b_%s.png" % tag)
        if not (frame_at(master, t, a) and frame_at(subbed, t, b)):
            continue
        ia = Image.open(a).convert("RGB")
        ib = Image.open(b).convert("RGB")
        m = ImageChops.difference(ia, ib).convert("L").point(lambda v: 255 if v > THRESH else 0)
        acc = m if acc is None else ImageChops.multiply(acc, m)
        used += 1
        for p in (a, b):
            if os.path.exists(p):
                os.remove(p)
    return acc, used


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:]
    n = 3
    all_cues = "--all" in argv
    argv = [a for a in argv if a != "--all"]
    if "--n" in argv:
        i = argv.index("--n")
        n = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    args = [a for a in argv if not a.startswith("--")]
    cfg = json.load(open(os.path.join(WS, "video-pipeline", "config.subtitle.json"),
                         encoding="utf-8"))
    CW, CH = cfg["canvas"]["width"], cfg["canvas"]["height"]
    lc = cfg["languages"]["zh"]
    bad = 0
    for ep in args:
        d = os.path.join(SUBS, ep)
        cues = json.load(open(os.path.join(d, "cues.json"), encoding="utf-8"))
        # ★ 画布以【实际母版】为准：高清放大后母版是 2560x1480，
        #   若还按配置里的 1440x832 判，字号/边距全都会误报"偏小"
        master_name = "%s_master_2k.mp4" % ep if os.path.exists(
            os.path.join(d, "%s_master_2k.mp4" % ep)) else "%s_master.mp4" % ep
        master = os.path.join(d, master_name)
        subbed = os.path.join(d, "%s_subbed.mp4" % ep)
        dims = _dims(master) or (CW, CH)
        W, H = dims
        BAND_H = int(round(H * BAND_RATIO))
        fs = int(round(H * lc["font_size_pct"] / 100.0))
        m_v = int(round(H * lc["margin_v_pct"] / 100.0))
        m_lr = int(round(W * lc["margin_lr_pct"] / 100.0))
        print("=" * 100)
        print("%s  母版 %s ｜ 画布 %dx%d ｜ 判定基准：字号 %dpx 底部间距 %dpx 左右边距 %dpx ｜ 字幕 %d 条"
              % (ep, master_name, W, H, fs, m_v, m_lr, len(cues)))
        # 默认抽查【最长文本】和【最短文本】（最易暴露断行/越界）；--all 则逐条全查
        if all_cues:
            pool = cues
        else:
            pool = sorted(cues, key=lambda c: -len("\n".join(c["lines"])))[:n]
            pool += sorted(cues, key=lambda c: len("\n".join(c["lines"])))[:1]
        seen = set()
        for c in pool:
            key = "\n".join(c["lines"])
            if key in seen:
                continue
            seen.add(key)
            mask, used = cue_mask(master, subbed, c, d, "x")
            if mask is None:
                print("  ❌ 抽帧失败：%s" % c["text"][:20])
                bad += 1
                continue
            full_bbox = mask.getbbox()
            # ★ 只在【字幕带】内度量：字幕永远在底部（margin_v 72px + 最多 140px 高）。
            #   不裁的话，重编码在画面中部造成的持续差异会把包围盒撑高
            #   （实测 ep06 t=7.16 被撑到 y[457,755] 高 298px，误报"偏大"）。
            band = mask.crop((0, H - BAND_H, W, H))
            bbox = band.getbbox()
            if not bbox:
                print("  ❌ t=%.2f 「%s」字幕带内无差异 —— 字幕没画出来？" % (c["start"], c["text"][:18]))
                bad += 1
                continue
            bx0, by0, bx1, by1 = bbox
            x0, x1 = bx0, bx1
            y0, y1 = H - BAND_H + by0, H - BAND_H + by1
            h, w = y1 - y0, x1 - x0
            nlines = len(c["lines"])
            ok_lr = x0 >= m_lr - 8 and x1 <= W - m_lr + 8
            ok_bot = abs((H - y1) - m_v) <= 16
            exp_h = fs * 1.2 * nlines + 2 * lc.get("outline", 3)
            ok_h = h <= exp_h + 14
            flag = "✅" if (ok_lr and ok_bot and ok_h) else "❌"
            if flag == "❌":
                bad += 1
            if all_cues and flag == "✅":
                # 全量模式：通过的打一行摘要，别把 100 多条刷满
                print("  ✅ t=%7.2f 行%d 高%3d 底%3d x[%d,%d]  %s"
                      % (c["start"], nlines, h, H - y1, x0, x1, c["text"][:24]))
                continue
            print("  %s t=%6.2f（%d 帧交集）" % (flag, c["start"], used))
            print("     文本  %s" % " / ".join(c["lines"]))
            print("     包围盒 x[%d,%d] 宽 %d ｜ y[%d,%d] 高 %d ｜ 底部留白 %dpx"
                  % (x0, x1, w, y0, y1, h, H - y1))
            print("     判定  行数 %d（配置 %d，实测高 %d ≤ 上限 %d%s）｜ 左右%s（边距 %d）"
                  % (nlines, lc["max_lines"], h, int(exp_h), "✅" if ok_h else " ❌偏大",
                     "在边距内✅" if ok_lr else "❌越界", m_lr))
            print("           底部 %s（配置 %d，实测 %d）"
                  % ("✅" if ok_bot else "❌偏差", m_v, H - y1))
    print("=" * 100)
    print("有问题的条目：%d" % bad)


if __name__ == "__main__":
    main()
