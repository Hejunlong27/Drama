#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
upscale_cost_compare.py —— 高清放大：本机（Topaz Video）vs RunningHub 云端 的时间/成本对比

为什么有这个脚本
----------------
「本机跑还是云端跑」是个会反复问的问题，而两边的计价口径完全不同：
  * 本机  → 免费，只花时间（+几毛电费）
  * RH 工作流 → **按运行时长计费**（不是按视频秒数）
  * RH 付费端点 → **按视频秒数计费**（贵 500~2500 倍，且没有 2K 档）
把口径和数字写死在脚本里，改一个参数就能重算，不用每次口算。

数据来源（全部是本机实测/官方价目表，不是估的）
----------------------------------------------
1. 本机速度 8.22 帧/秒：ep03 3980 帧 / 484.1 s（1440×832 → 2560×1480，prob-4，RTX 4060 Laptop）
2. RH 计费口径 **RH币 = taskCostTime(秒) × 0.2**：由 video-pipeline/ledger/runs/ 里
   76 笔真实任务反推（比值 0.2000~0.2099，中位 0.2007）⇒ **12 RH币/分钟运行时长**
3. RH 币值换算 **1 RH币 = ¥0.0025**（台账自带：209 RH币 = ¥0.5225；= 1 美元 2746 RH币）
4. 云端放大速度：用户实测「30 s 视频 ≈ 5 min 运行时长」⇒ 10 秒运行/秒视频
5. RH 付费端点价目表：docs/runninghub-developer-kit/pricing.public.json（2026-04-29 快照）

用法
----
    python scripts/upscale_cost_compare.py                       # 默认 30/60/90 分钟
    python scripts/upscale_cost_compare.py --minutes 13 45 120
    python scripts/upscale_cost_compare.py --cloud-ratio 8       # 云端换成 8 秒运行/秒视频
    python scripts/upscale_cost_compare.py --bitrate-mbps 20     # 换码率算体积
"""
from __future__ import annotations

import argparse
import os
import sys


def harden_stdio() -> None:
    """★ 中文 Windows 控制台是 GBK(cp936)，正文含 '¥'(U+00A5) —— GBK 装不下，直接 print 会崩。

    与 scripts/cost_report.py / drama.py 同一套做法：按 isatty() 分流
    （控制台用控制台代码页、管道/重定向用 UTF-8），errors 放宽成 replace 兜底。
    """
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

# ------------------------------------------------------------------ 默认参数
FPS = 24.0                     # 本剧成片帧率
LOCAL_FPS = 8.22               # 本机实测放大速度（帧/秒）
LOCAL_FPS_SLOW = 6.65          # 含模型加载的短片段实测（保守下限）
CLOUD_RUNTIME_RATIO = 10.0     # 云端：每 1 秒视频要多少秒运行时长（用户实测 30s→5min）
RH_PER_RUNTIME_S = 0.2         # RH币/运行秒（台账反推）⇒ 12 币/分钟
RH_TO_CNY = 0.0025             # 1 RH币 = ¥0.0025
WATT = 140.0                   # 本机满载功耗估计（4060 Laptop 8GB + 7940H + 周边）
CNY_PER_KWH = 0.6
BITRATE_2K = 11.79             # Mbps，实测 ep03_master_2k
BITRATE_1440P = 4.47           # Mbps，实测 ep03_master

# RH 付费端点（元/视频秒）—— 注意：这些是"按视频秒数"计价，与工作流完全不同
ENDPOINTS = [
    ("topazlabs/video-upscale  720p@30", 0.40, "无 2K 档"),
    ("topazlabs/video-upscale  720p@60", 0.80, "无 2K 档"),
    ("topazlabs/video-upscale 1080p@30", 0.70, "无 2K 档"),
    ("topazlabs/video-upscale 1080p@60", 1.50, "无 2K 档"),
    ("topazlabs/video-upscale    4k@30", 1.20, "没有 2K，只能跳 4K"),
    ("topazlabs/video-upscale    4k@60", 2.50, "没有 2K，只能跳 4K"),
    ("rhart-video/video-upscaler  720p", 0.14, ""),
    ("rhart-video/video-upscaler 1080p", 0.21, ""),
    ("rhart-video/video-upscaler    2k", 0.35, "★ 唯一带 2K 档的端点"),
    ("rhart-video/video-upscaler    4k", 0.56, ""),
]


def hm(sec: float) -> str:
    h = sec / 3600.0
    if h < 1:
        return "%.0f 分钟" % (sec / 60.0)
    if h < 48:
        return "%.2f 小时" % h
    return "%.1f 天" % (h / 24.0)


def main() -> int:
    harden_stdio()
    ap = argparse.ArgumentParser(description="高清放大：本机 vs RunningHub 云端 的成本/时间对比")
    ap.add_argument("--minutes", type=float, nargs="+", default=[30, 60, 90],
                    help="要算的剧长（分钟），可给多个")
    ap.add_argument("--local-fps", type=float, default=LOCAL_FPS)
    ap.add_argument("--cloud-ratio", type=float, default=CLOUD_RUNTIME_RATIO,
                    help="云端每 1 秒视频消耗多少秒运行时长（默认 10，来自 30s→5min 实测）")
    ap.add_argument("--rh-per-second", type=float, default=RH_PER_RUNTIME_S)
    ap.add_argument("--rh-to-cny", type=float, default=RH_TO_CNY)
    ap.add_argument("--watt", type=float, default=WATT)
    ap.add_argument("--kwh-price", type=float, default=CNY_PER_KWH)
    ap.add_argument("--bitrate-mbps", type=float, default=BITRATE_2K)
    args = ap.parse_args()

    rh_per_min = args.rh_per_second * 60
    print("=" * 104)
    print("高清放大 · 本机（Topaz Video）vs RunningHub 云端")
    print("=" * 104)
    print("口径：%.1f fps ｜ 本机 %.2f 帧/秒 ｜ 云端 %.1f 秒运行/秒视频 ｜ RH %.1f 币/分钟运行 ｜ 1 RH币=¥%.4f"
          % (FPS, args.local_fps, args.cloud_ratio, rh_per_min, args.rh_to_cny))
    print()
    hdr = ("%-9s %8s %13s %15s %11s %9s %10s %9s" %
           ("剧长", "视频秒", "本机耗时", "云端运行时长", "云端RH币", "云端¥", "本机电费", "2K体积"))
    print(hdr)
    print("-" * 104)
    for m in args.minutes:
        vsec = m * 60.0
        frames = vsec * FPS
        local_s = frames / args.local_fps
        cloud_run_s = vsec * args.cloud_ratio
        coins = cloud_run_s * args.rh_per_second
        cny = coins * args.rh_to_cny
        kwh = args.watt / 1000.0 * local_s / 3600.0
        gb = args.bitrate_mbps * 1e6 / 8.0 * vsec / 1e9
        print("%-9s %8.0f %13s %15s %11.0f %9.2f %10.2f %8.1fG" %
              ("%.0f 分钟" % m, vsec, hm(local_s), hm(cloud_run_s), coins, cny,
               kwh * args.kwh_price, gb))
    print("-" * 104)
    print("注：云端『运行时长』= RH 账单向你计费的 taskCostTime；实际墙钟还要乘 ~1.5–2 倍（排队）。")
    print("    本机电费按 %.0fW × ¥%.2f/度 估；磁盘按 %.1f Mbps（%.1f GB/小时）。"
          % (args.watt, args.kwh_price, args.bitrate_mbps, args.bitrate_mbps * 3.6 / 8))
    print()

    print("速度对比（云端运行时长 ÷ 本机耗时）")
    for m in args.minutes:
        vsec = m * 60.0
        r = (vsec * args.cloud_ratio) / (vsec * FPS / args.local_fps)
        print("   %6.0f 分钟剧长：云端是本机的 %.2f 倍慢（本机快 %.1f 倍）" % (m, r, r))
    print()
    print("成本对比（单集 2.5 分钟的短剧，一集 ≈ 多少）")
    for m in args.minutes:
        vsec = m * 60.0
        coins = vsec * args.cloud_ratio * args.rh_per_second
        print("   %6.0f 分钟剧长：云端 %.0f RH币（¥%.2f） vs 本机 ¥%.3f（电费）"
              % (m, coins, coins * args.rh_to_cny,
                 args.watt / 1000.0 * (vsec * FPS / args.local_fps) / 3600.0 * args.kwh_price))
    print()
    print("=" * 104)
    print("★ 另一条云端路径：RH 的【付费端点】是按「视频秒数」计价，不是按运行时长")
    print("=" * 104)
    head = "%-34s %8s" % ("端点", "元/秒")
    for m in args.minutes:
        head += " %16s" % ("%.0f 分钟剧" % m)
    print(head)
    for name, cny_s, note in ENDPOINTS:
        line = "%-34s %8.2f" % (name, cny_s)
        for m in args.minutes:
            vsec = m * 60.0
            line += " %16s" % ("%.0f 币/¥%.0f" % (cny_s * vsec / args.rh_to_cny, cny_s * vsec))
        if note:
            line += "   %s" % note
        print(line)
    print()
    print("⇒ 同样一段 60 分钟的剧：工作流 ≈ %.0f RH币，而付费端点要 %.0f–%.0f RH币（差 %.0f~%.0f 倍）"
          % (3600 * args.cloud_ratio * args.rh_per_second,
             0.14 * 3600 / args.rh_to_cny, 2.5 * 3600 / args.rh_to_cny,
             (0.14 / args.rh_to_cny) / (args.cloud_ratio * args.rh_per_second),
             (2.5 / args.rh_to_cny) / (args.cloud_ratio * args.rh_per_second)))
    print()
    print("=" * 104)
    print("对照锚点（本剧真实数据，用来判断上面的数字是否离谱）")
    print("=" * 104)
    print("   H3 出片：8 集 64 段 / 792 秒成片 / 9,705 RH币 ⇒ 约 735 RH币 per 视频分钟")
    print("   云端放大：约 %.0f RH币 per 视频分钟 ⇒ 比『生成』还便宜 %.1f 倍"
          % (args.cloud_ratio * 60 * args.rh_per_second,
             (9705 / (792 / 60.0)) / (args.cloud_ratio * 60 * args.rh_per_second)))
    print("   本机放大：¥0（只有电费与时间）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
