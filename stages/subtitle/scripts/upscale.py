#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
upscale.py —— 高清放大（Topaz Video 本地 AI 超分）· 短剧流水线「合并之后」的一道工序

为什么需要它
------------
H3 出片是 1440x832。这个尺寸在手机竖屏/大屏播放时不够看，而**烧字幕会重编码**：
如果先烧字幕再放大，字幕会跟着被"AI 重绘"（描边发糊、字被当成纹理处理）。
⇒ 正确顺序是【合并母版 → 放大到 2K → 再问要不要字幕 → 在 2K 上烧字幕】。
本脚本负责中间那一步，产出 <ep>_master_2k.mp4，字幕管线接着拿它当母版。

它跟 Topaz 官方 GUI 的关系
--------------------------
Topaz Video（D:\\Program Files\\Topaz Labs LLC\\Topaz Video）自带一个**带 tvai_up 滤镜的 ffmpeg**，
GUI 导出时内部就是在调它。本脚本复刻 GUI 的命令行，因此**结果与 GUI 一致**，但可批处理、可断言。

★ 本机实测踩到的坑（2026-09-21）：直接跑那个 ffmpeg 会报 `Model not found: prob-4`。
  原因不是没买/没装，而是**模型权重被放在 D 盘**（用户把 Topaz 的模型目录挪到了 D:），
  而独立运行的 ffmpeg 默认只看 C:\\ProgramData\\...\\models（那里只有模型定义 json，没有权重 .tz3）。
  解法 = 给子进程设两个环境变量（库内部读的就是它们，从 videoai.dll 里挖出来的）：
      TVAI_MODEL_DIR      -> 模型定义目录（*.json，通常在 C:\\ProgramData\\Topaz Labs LLC\\Topaz Video\\models）
      TVAI_MODEL_DATA_DIR -> 模型权重目录（*.tz3，本机在 D:\\ProgramData\\Topaz Labs LLC\\Topaz Video\\models）
  本脚本会自动探测这两个目录，也可在 config.upscale.json 里写死。

用法
----
    python scripts/upscale.py --ep ep03                 # 放大 ep03 母版到 2K
    python scripts/upscale.py --ep ep03 --dry-run       # 只打印将要执行的命令与预计耗时
    python scripts/upscale.py --ep ep03 --verify-only   # 只对已有产物做断言
    python scripts/upscale.py --ep ep03 --force         # 忽略缓存重跑
    python scripts/upscale.py --ep ep03 --target 3840x2160
    python scripts/upscale.py --ep ep03 --fallback-lanczos   # Topaz 不可用时退到插值（画质不提升，会明确告警）
    python scripts/upscale.py --ep ep03 --json          # 机器可读输出（给上层编排用）

产物（与字幕管线同一个目录）
    <ep>_master_2k.mp4      放大后的母版（后续字幕就烧在它上面）
    <ep>_upscale.json       本次放大的参数 + 全部自检数据（可复核）
    <ep>_upscale.log        ffmpeg 原始日志

配置
    <工作区>/video-pipeline/config.upscale.json —— 改目标分辨率/模型/编码参数，不改代码。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

_HINT = (
    "找不到短剧工作区。请二选一：\n"
    "  ① 命令行加 --workspace \"<项目根目录>\"\n"
    "  ② 设环境变量：$env:DRAMA_WORKSPACE = \"<项目根目录>\"  （PowerShell）\n"
    "工作区的标志物是 <项目根>/video-pipeline/data/。")


def resolve_ws():
    """工作区解析：`_ws`（项目里有）> DRAMA_WORKSPACE > 脚本位置推断 > None。

    ★ 为什么不能简单 `from _ws import WS`：本脚本住在 **Skill 包**里时没有 `_ws.py`
    （它属于另一个 Skill），会直接 ModuleNotFoundError —— 而且 `_ws` 在解析不到工作区时
    抛的是 **SystemExit**（不是 Exception），不接住会把宿主进程一起带走。
    实测踩过：从 Skill 目录调用报 `No module named '_ws'`。
    ⇒ 这里做成「能用就用、不能用就按同样的优先级自己推」。
    """
    d = os.path.dirname(os.path.abspath(__file__))
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        from _ws import WS as _WS          # noqa
        return str(_WS), ""
    except BaseException:
        pass
    env = os.environ.get("DRAMA_WORKSPACE")
    if env and os.path.isdir(env):
        return os.path.abspath(env), ""
    cand = os.path.dirname(d)
    if os.path.isdir(os.path.join(cand, "video-pipeline", "data")):
        return cand, ""
    return None, _HINT


WS, WS_HINT = resolve_ws()

EXE_SUFFIX = ".exe"
FFMPEG_NAME = "ffmpeg" + EXE_SUFFIX
FFPROBE_NAME = "ffprobe" + EXE_SUFFIX

# 模型定义目录（*.json）的候选位置；权重目录（*.tz3）另行全盘探测
MODEL_DIR_CANDIDATES = [
    r"C:\ProgramData\Topaz Labs LLC\Topaz Video\models",
    r"C:\ProgramData\Topaz Labs LLC\Topaz Video AI\models",
    r"D:\ProgramData\Topaz Labs LLC\Topaz Video\models",
    r"D:\ProgramData\Topaz Labs LLC\Topaz Video AI\models",
]

# 权重目录的扫描根：<盘>:\ProgramData\Topaz Labs LLC\Topaz Video\models（用户把模型挪走时会在别的盘）
DATA_DIR_SCAN_ROOTS = [
    r"C:\ProgramData\Topaz Labs LLC",
    r"D:\ProgramData\Topaz Labs LLC",
    r"E:\ProgramData\Topaz Labs LLC",
]

TOPAZ_INSTALL_CANDIDATES = [
    r"D:\Program Files\Topaz Labs LLC\Topaz Video",
    r"C:\Program Files\Topaz Labs LLC\Topaz Video",
    r"D:\Program Files\Topaz Labs LLC\Topaz Video AI",
    r"C:\Program Files\Topaz Labs LLC\Topaz Video AI",
]


# ---------------------------------------------------------------- 基础设施

def harden_stdio():
    """Windows 控制台是 GBK，直接打印非 GBK 字符会崩（本项目已踩过多次）。"""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def say(msg=""):
    print(msg, flush=True)


def load_json(p, default=None):
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def dump_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


DEFAULT_CONFIG = {
    "_note": "高清放大配置。target.mode=width 表示『按宽度等比放大』（本机 1440x832 -> 2560x1480，不变形）。",
    "version": 1,
    "enabled": True,
    "tool": {
        "_note": "留空则自动探测 Topaz Video 安装目录与模型目录；探测不到会在报错里给出可照抄的改法",
        "install_dir": "",
        "ffmpeg": "",
        "ffprobe": "",
        "model_dir": "",
        "model_data_dir": "",
        "extra_env": {},
    },
    "target": {
        "mode": "width",
        "width": 2560,
        "scale": 2.0,
        "keep_aspect": True,
        "even": True,
        "fallback_to_exact_none": True,
    },
    "model": {
        "name": "prob-4",
        "_note": "prob-4=Proteus 4（通用实拍/AI 生成人物最稳，本机 GUI 实测用的就是它）。其它可选：iris-3=人脸修复、rhea-1=4 倍、nyx-3=降噪",
        "scale": 0,
        "estimate": 8,
        "blend": 0.2,
        "preblur": 0.0,
        "noise": 0.0,
        "details": 0.0,
        "halo": 0.0,
        "blur": 0.0,
        "compression": 0.0,
        "device": 0,
        "vram": 1,
        "instances": 1,
    },
    "encode": {
        "_note": "h264_nvenc + constqp 18 与 Topaz GUI 完整导出同参；要更小体积可换 hevc_nvenc",
        "codec": "h264_nvenc",
        "preset": "p5",
        "rc": "constqp",
        "qp": 18,
        "profile": "high",
        "pix_fmt": "yuv420p",
        "audio": "copy",
    },
    "verify": {
        "samples": 3,
        "min_frame_gain_hf": 1.02,
        "min_frame_gain_lap": 1.05,
        "flush_warn": True,
    },
    # 只用于 --dry-run 的耗时预估（实测值，RTX 4060 Laptop 跑 prob-4 到 2K）
    "perf": {"expected_fps": 8.2},
}


def deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(ws, path=None):
    p = path or os.path.join(ws, "video-pipeline", "config.upscale.json")
    cfg = deep_merge(DEFAULT_CONFIG, load_json(p, {}) or {})
    cfg["_config_path"] = p
    cfg["_config_exists"] = os.path.exists(p)
    return cfg


# ---------------------------------------------------------------- 工具与模型探测

def _first_existing(paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def detect_toolchain(cfg):
    """定位 Topaz 的 ffmpeg/ffprobe + 模型定义目录 + 模型权重目录。

    返回 (info, problems)：problems 非空时调用方负责给出可照抄的修法。
    """
    t = cfg["tool"]
    problems = []
    install = t.get("install_dir") or os.environ.get("TOPAZ_VIDEO_DIR") or ""
    if install and not os.path.isdir(install):
        problems.append("配置的 install_dir 不存在：%s" % install)
        install = ""
    if not install:
        install = _first_existing(TOPAZ_INSTALL_CANDIDATES) or ""

    ff = t.get("ffmpeg") or (os.path.join(install, FFMPEG_NAME) if install else "")
    fp = t.get("ffprobe") or (os.path.join(install, FFPROBE_NAME) if install else "")
    if not ff or not os.path.exists(ff):
        problems.append("找不到 Topaz 自带的 ffmpeg（它才带 tvai_up 滤镜）")
    if not fp or not os.path.exists(fp):
        fp = "ffprobe"          # 系统 ffprobe 也够用

    # 模型定义目录（*.json）
    mdir = t.get("model_dir") or os.environ.get("TVAI_MODEL_DIR") or ""
    if not mdir or not os.path.isdir(mdir):
        mdir = _first_existing(MODEL_DIR_CANDIDATES) or mdir
    if not mdir or not os.path.isdir(mdir):
        problems.append("找不到 Topaz 模型定义目录（内含 prob-4.json 等）")

    # 模型权重目录（*.tz3）
    ddir = t.get("model_data_dir") or os.environ.get("TVAI_MODEL_DATA_DIR") or ""
    if not ddir or not _has_weights(ddir):
        found = _scan_for_weight_dir()
        if found:
            ddir = found
    if not ddir or not _has_weights(ddir):
        problems.append("找不到 Topaz 模型权重目录（内含 *.tz3）。"
                        "通常是模型被挪到了别的盘 —— 见 docs/高清放大-2K-实测与操作手册.md")

    return {
        "install_dir": install,
        "ffmpeg": ff, "ffprobe": fp,
        "model_dir": mdir, "model_data_dir": ddir,
        "extra_env": t.get("extra_env") or {},
    }, problems


def _has_weights(d):
    if not d or not os.path.isdir(d):
        return False
    try:
        for name in os.listdir(d):
            if name.endswith(".tz3"):
                return True
    except OSError:
        pass
    return False


def _scan_for_weight_dir():
    """在候选根下找『装着 *.tz3 的 models 目录』（深度 ≤2，够用且快）。"""
    for root in DATA_DIR_SCAN_ROOTS:
        if not os.path.isdir(root):
            continue
        for sub in ("Topaz Video", "Topaz Video AI"):
            for cand in (os.path.join(root, sub, "models"), os.path.join(root, sub)):
                if _has_weights(cand):
                    return cand
    return ""


def model_weight_files(tools, model_name):
    """统计该模型在权重目录里有多少个 *.tz3（0 = 权重没下载）。"""
    d = tools.get("model_data_dir") or ""
    if not _has_weights(d):
        return 0, []
    # 模型名 prob-4 -> 文件名前缀 prob-v4-
    m = re.match(r"^([a-z]+)-?(\d+)?$", model_name or "")
    prefixes = []
    if m:
        base, ver = m.group(1), m.group(2)
        if ver:
            prefixes.append("%s-v%s-" % (base, ver))
            prefixes.append("%s-%s-" % (base, ver))
        prefixes.append(base)
    hits = []
    try:
        for name in os.listdir(d):
            if name.endswith(".tz3") and any(name.startswith(p) for p in prefixes):
                hits.append(name)
    except OSError:
        pass
    return len(hits), sorted(hits)[:5]


def probe_filter(tools):
    """确认这个 ffmpeg 真的带 tvai_up 滤镜（否则一切白说）。"""
    try:
        p = subprocess.run([tools["ffmpeg"], "-hide_banner", "-filters"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        return b"tvai_up" in p.stdout
    except Exception:
        return False


# ---------------------------------------------------------------- 尺寸与命令

def ffprobe_json(tools, path, entries):
    cmd = [tools["ffprobe"], "-v", "error", "-show_entries", entries,
           "-of", "json", path]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.stdout.decode("utf-8", errors="replace")
    try:
        return json.loads(out)
    except Exception:
        raise RuntimeError("ffprobe 解析失败：%s\n%s" % (path, out[-500:]))


def video_info(tools, path):
    j = ffprobe_json(tools, path, "stream=index,codec_type,codec_name,width,height,"
                                  "r_frame_rate,nb_frames,duration")
    v = a = None
    for s in j.get("streams", []):
        if s.get("codec_type") == "video" and v is None:
            v = s
        elif s.get("codec_type") == "audio" and a is None:
            a = s
    if not v:
        raise RuntimeError("没有视频流：%s" % path)
    fmt = ffprobe_json(tools, path, "format=duration,size,bit_rate").get("format", {})

    def num(x, d=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return d
    return {
        "path": path, "width": int(v["width"]), "height": int(v["height"]),
        "fps": num(str(v.get("r_frame_rate", "0/1")).split("/")[0]) /
               max(num(str(v.get("r_frame_rate", "0/1")).split("/")[-1], 1), 1),
        "nb_frames": int(num(v.get("nb_frames"))),
        "v_duration": num(v.get("duration"), num(fmt.get("duration"))),
        "duration": num(fmt.get("duration")),
        "codec": v.get("codec_name"),
        "audio_codec": (a or {}).get("codec_name"),
        "a_duration": num((a or {}).get("duration"), num(fmt.get("duration"))),
        "a_nb_frames": int(num((a or {}).get("nb_frames"))),
        "size": int(num(fmt.get("size"))),
    }


def count_frames(tools, path):
    """实数帧数（不信容器声明）——与字幕管线同一套口径。"""
    p = subprocess.run([tools["ffprobe"], "-v", "error", "-select_streams", "v:0",
                        "-count_frames", "-show_entries", "stream=nb_read_frames",
                        "-of", "csv=p=0", path],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.stdout.decode("utf-8", errors="replace").strip()
    try:
        return int(out.splitlines()[0].strip('"'))
    except Exception:
        return 0


def target_size(src, tgt):
    """算目标分辨率。mode=width 时等比（必要时取偶），保证不变形。"""
    mode = tgt.get("mode", "width")
    w, h = src["width"], src["height"]
    if mode == "exact":
        return int(tgt["width"]), int(tgt["height"]), 1.0
    if mode == "factor":
        f = float(tgt.get("scale", 2.0))
    else:
        W = int(tgt.get("width", 2560))
        f = W / float(w)
    if tgt.get("keep_aspect", True):
        tw, th = int(round(w * f)), int(round(h * f))
    else:
        tw = int(round(w * f))
        th = int(round(h * f))
    if tgt.get("even", True):
        tw += tw % 2
        th += th % 2
    return tw, th, f


def build_filter(cfg, tw, th):
    m = cfg["model"]
    # ★ tvai_up 的**参数之间用冒号**、滤镜之间用逗号 —— 写错会被当成未知滤镜参数
    #   （实测踩过：全用逗号 → "No such filter"，或者参数被静默忽略）
    spec = "tvai_up=model=%s:scale=%d:w=%d:h=%d" % (m["name"], int(m.get("scale", 0)), tw, th)
    for key in ("preblur", "noise", "details", "halo", "blur", "compression",
                "estimate", "blend", "device", "vram", "instances"):
        if m.get(key) is not None:
            spec += ":%s=%s" % (key, _fmt(m[key]))
    # 末尾的 scale 把结果精确钉到目标尺寸（Topaz GUI 也这么做）
    return "%s,scale=w=%d:h=%d:flags=lanczos:threads=0" % (spec, tw, th)


def _fmt(v):
    if isinstance(v, float):
        s = ("%.4f" % v).rstrip("0").rstrip(".")
        return s if s else "0"
    return str(v)


def build_cmd(cfg, tools, src, dst, tw, th):
    e = cfg["encode"]
    cmd = [tools["ffmpeg"], "-hide_banner", "-nostdin", "-y", "-i", src,
           "-sws_flags", "spline+accurate_rnd+full_chroma_int",
           "-filter_complex", build_filter(cfg, tw, th)]
    if e["codec"] == "libx264":
        cmd += ["-c:v", e["codec"], "-crf", str(e.get("crf", 18)),
                "-preset", str(e.get("preset", "medium"))]
    else:
        cmd += ["-c:v", e["codec"], "-preset", str(e.get("preset", "p5"))]
        if e.get("rc") == "constqp":
            cmd += ["-rc", "constqp", "-qp", str(e.get("qp", 18))]
        else:
            cmd += ["-b:v", str(e.get("bitrate", "12M"))]
        if e.get("profile"):
            cmd += ["-profile:v", str(e["profile"])]
    cmd += ["-pix_fmt", e.get("pix_fmt", "yuv420p")]
    # ★ 千万不要写 `-map 0:v:0`：ffmpeg 会先把输入视频流"预定"给显式 map，
    #   于是滤镜图里那个未命名的 tvai_up 输入垫拿不到流，报
    #   "Cannot find an unused video input stream to feed the unlabeled input pad tvai_up:default"
    #   （2026-09-21 实测踩到；Topaz GUI 自身也只 map 音频）
    if e.get("audio", "copy") == "copy":
        cmd += ["-map", "0:a?", "-c:a", "copy"]
    else:
        cmd += ["-map", "0:a?", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-fps_mode:v", "passthrough", "-movflags", "+faststart", dst]
    return cmd


# ---------------------------------------------------------------- 自检

def sample_frames(tools, path, times, outdir):
    os.makedirs(outdir, exist_ok=True)
    got = {}
    for i, t in enumerate(times):
        dst = os.path.join(outdir, "_up_s_%d.png" % i)
        if os.path.exists(dst):
            os.remove(dst)
        subprocess.run([tools["ffmpeg"], "-hide_banner", "-nostats", "-v", "error", "-y",
                        "-ss", "%.3f" % t, "-i", path, "-frames:v", "1", dst],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(dst):
            got[round(t, 3)] = dst
    return got


def frame_metrics(png):
    """清晰度指标（无 numpy/PIL 时返回 None）。"""
    try:
        import numpy as np
        from PIL import Image
    except Exception:
        return None
    a = np.asarray(Image.open(png).convert("L"), dtype=np.float64)
    k = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)
    from numpy.lib.stride_tricks import sliding_window_view
    if min(a.shape) < 8:
        lap = 0.0
    else:
        w = sliding_window_view(a, (3, 3))
        lap = float(np.einsum("ijkl,kl->ij", w, k).var())
    F = np.abs(np.fft.fftshift(np.fft.fft2(a)))
    h, wd = a.shape
    cy, cx = h // 2, wd // 2
    R = min(h, wd) // 8
    Y, X = np.ogrid[:h, :wd]
    mask = ((Y - cy) ** 2 + (X - cx) ** 2) > R * R
    hf = float(F[mask].mean() / F.mean()) if F.mean() else 0.0
    return {"lap_var": round(lap, 3), "hf_ratio": round(hf, 4),
            "mean": round(float(a.mean()), 2), "max": int(a.max())}


def verify(tools, cfg, src_path, dst_path, tmpdir, do_sharpness=True):
    """全部机器断言：尺寸/帧数/时长/音轨/抽样帧非黑 + 比插值更锐。"""
    v = {"checks": [], "passed": True}

    def check(name, ok, detail):
        v["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            v["passed"] = False
        return ok

    src = video_info(tools, src_path)
    dst = video_info(tools, dst_path)
    v["src"] = src
    v["dst"] = dst

    want_w, want_h, _ = target_size(src, cfg["target"])
    check("分辨率精确匹配目标", (dst["width"], dst["height"]) == (want_w, want_h),
          "期望 %dx%d，实际 %dx%d" % (want_w, want_h, dst["width"], dst["height"]))
    check("宽高均为偶数（yuv420p 硬要求）",
          dst["width"] % 2 == 0 and dst["height"] % 2 == 0,
          "%dx%d" % (dst["width"], dst["height"]))

    sf = count_frames(tools, src_path)
    df = count_frames(tools, dst_path)
    v["src_frames"], v["dst_frames"] = sf, df
    check("帧数与源一致", sf == df and df > 0, "源 %d 帧 / 新 %d 帧" % (sf, df))
    check("时长一致（±0.2s）", abs(dst["v_duration"] - src["v_duration"]) <= 0.2,
          "源 %.2fs / 新 %.2fs" % (src["v_duration"], dst["v_duration"]))
    check("音轨保留且时长一致（±0.2s）",
          bool(dst["audio_codec"]) and abs(dst["a_duration"] - src["a_duration"]) <= 0.2,
          "源 %s %.2fs / 新 %s %.2fs" % (src["audio_codec"], src["a_duration"],
                                         dst["audio_codec"], dst["a_duration"]))

    # 抽样帧：不许是黑帧/绿帧，且必须比纯 lanczos 插值更锐（否则模型根本没生效）
    n = int(cfg["verify"].get("samples", 3)) or 3
    dur = max(dst["v_duration"], 0.1)
    times = [round(dur * (i + 0.5) / n, 3) for i in range(n)]
    up = sample_frames(tools, dst_path, times, tmpdir)
    smp = sample_frames(tools, src_path, times, tmpdir + "_src")
    rows = []
    for t, png in sorted(up.items()):
        m = frame_metrics(png)
        if m is None:
            rows.append({"t": t, "skipped": "缺少 numpy/Pillow"})
            continue
        row = {"t": t, "up": m}
        sp = smp.get(t)
        if sp and do_sharpness:
            try:
                from PIL import Image
                im = Image.open(sp).convert("RGB").resize(
                    (dst["width"], dst["height"]), Image.LANCZOS)
                lan_png = os.path.join(tmpdir, "_up_lan_%.3f.png" % t)
                im.save(lan_png)
                row["lanczos"] = frame_metrics(lan_png)
                os.remove(lan_png)
            except Exception as e:
                row["lanczos"] = {"error": str(e)}
        rows.append(row)
    v["frames"] = rows

    black = [r for r in rows if "up" in r and r["up"]["max"] < 16]
    check("抽样帧不是黑帧/绿帧", not black, "黑帧时间点：%s" % ([r["t"] for r in black] or "无"))

    gain_hf, gain_lap = [], []
    for r in rows:
        lz = r.get("lanczos") or {}
        if "hf_ratio" in lz and "up" in r:
            if lz["hf_ratio"]:
                gain_hf.append(r["up"]["hf_ratio"] / lz["hf_ratio"])
            if lz["lap_var"]:
                gain_lap.append(r["up"]["lap_var"] / lz["lap_var"])
    if gain_hf and gain_lap:
        v["sharpness_gain_hf"] = round(sum(gain_hf) / len(gain_hf), 4)
        v["sharpness_gain_lap"] = round(sum(gain_lap) / len(gain_lap), 4)
        need_hf = float(cfg["verify"].get("min_frame_gain_hf", 1.02))
        need_lap = float(cfg["verify"].get("min_frame_gain_lap", 1.05))
        check("确实比纯插值更锐（说明 AI 模型真的生效了）",
              v["sharpness_gain_hf"] >= need_hf and v["sharpness_gain_lap"] >= need_lap,
              "相对 lanczos：高频 x%.3f（需 ≥%.2f）／拉普拉斯方差 x%.3f（需 ≥%.2f）"
              % (v["sharpness_gain_hf"], need_hf, v["sharpness_gain_lap"], need_lap))
    return v


# ---------------------------------------------------------------- 主流程

def _file_fingerprint(path, chunk=1 << 20):
    """文件指纹 = 大小 + 头尾各 1MB 的 md5。

    ★ 为什么不用 mtime：`subtitle.py --stage all` 每次都重跑一遍「无损拼接」，
    母版内容一模一样但 mtime 变了 —— 用 mtime 当缓存键会导致**每跑一次都重放大一遍**
    （实测代价：8 分钟 GPU 时间白烧）。
    拼接是无损的、对同一批段是可复现字节的 ⇒ 用「内容指纹」当键既准又省。
    """
    size = os.path.getsize(path)
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read(chunk))
        if size > chunk * 2:
            f.seek(-chunk, os.SEEK_END)
            h.update(f.read(chunk))
    return size, h.hexdigest()


def cache_key(cfg, tools, src, tw, th):
    size, fp = _file_fingerprint(src)
    src_info = video_info(tools, src)
    blob = json.dumps({
        "src": [os.path.basename(src), size, fp],
        "src_info": [src_info["width"], src_info["height"], src_info["nb_frames"],
                     round(src_info["duration"], 3)],
        "target": [tw, th], "model": cfg["model"], "encode": cfg["encode"],
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def run_ffmpeg(cmd, env, log_path, total_frames, quiet=False):
    """跑 Topaz ffmpeg 并回报进度（用户明确要求长操作必须有进度）。"""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    t0 = time.time()
    logf = open(log_path, "w", encoding="utf-8", errors="replace")
    logf.write(" ".join(cmd) + "\n\n")
    logf.flush()
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT)
    last = -1
    tail = []
    for raw in p.stdout:
        line = raw.decode("utf-8", errors="replace")
        logf.write(line)
        tail.append(line)
        if len(tail) > 200:
            tail.pop(0)
        m = re.search(r"frame=\s*(\d+)", line)
        if m and total_frames:
            n = int(m.group(1))
            pct = int(n * 100 / total_frames)
            if pct >= last + 5:
                last = pct
                el = time.time() - t0
                eta = (el / max(n, 1)) * max(total_frames - n, 0)
                if not quiet:
                    say("      %3d%%  %d/%d 帧  已用 %.0fs  预计还需 %.0fs"
                        % (pct, n, total_frames, el, eta))
    p.wait()
    logf.close()
    return p.returncode, "".join(tail[-40:]), round(time.time() - t0, 1)


def upscale_episode(ws, ep, cfg, *, force=False, dry_run=False, verify_only=False,
                    fallback_lanczos=False, target_override=None, quiet=False,
                    allow_flat=False, sample=0.0, out_path=None):
    subs_dir = os.path.join(ws, "video-pipeline", "output", "subs", ep)
    src = os.path.join(subs_dir, "%s_master.mp4" % ep)
    dst = out_path or os.path.join(subs_dir, "%s_master_2k.mp4" % ep)
    # 自定义输出（测速样片）时，报告也写到旁边去，绝不覆盖正式产物的 <ep>_upscale.json
    report_path = (dst + ".json") if out_path else os.path.join(
        subs_dir, "%s_upscale.json" % ep)
    log_path = (os.path.splitext(dst)[0] + ".ffmpeg.log") if out_path else os.path.join(
        subs_dir, "%s_upscale.log" % ep)
    tmpdir = os.path.join(subs_dir, "_up_tmp")

    result = {"episode": ep, "src": src, "dst": dst, "ok": False, "actions": []}
    if not os.path.exists(src):
        result["error"] = "找不到母版（先跑 subtitle.py --stage master）：%s" % src
        return result

    # ★ 测速样片：只取母版前 N 秒。为什么先落盘成一个小文件再跑：
    #   这样后面所有断言（帧数一致 / 时长一致 / 音轨一致）口径完全不变，
    #   否则"源 3805 帧 vs 样片 1440 帧"会直接判失败。
    sample_src = None
    if sample and sample > 0:
        sample_src = os.path.splitext(dst)[0] + ".src.mp4"
        os.makedirs(os.path.dirname(sample_src), exist_ok=True)
        r = subprocess.run([cfg["tool"].get("ffmpeg") or "ffmpeg", "-hide_banner",
                            "-v", "error", "-y", "-t", "%.3f" % sample, "-i", src,
                            "-c", "copy", sample_src],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if not os.path.exists(sample_src):
            result["error"] = "抽测速样片失败：%s" % r.stdout.decode("utf-8", "replace")[-300:]
            return result
        src = sample_src
        result["sample_seconds"] = sample
        result["actions"].append("测速模式：只用母版前 %.1f 秒" % sample)

    tools, problems = detect_toolchain(cfg)
    result["tools"] = tools
    src_info = None
    if os.path.exists(tools.get("ffprobe") or "ffprobe"):
        try:
            src_info = video_info(tools, src)
        except Exception as e:
            result["error"] = "读源视频失败：%s" % e
            return result
    result["source_info"] = src_info
    if src_info and target_override:
        cfg = deep_merge(cfg, {"target": target_override})

    tw, th, factor = target_size(src_info, cfg["target"]) if src_info else (0, 0, 0)
    result["target"] = {"width": tw, "height": th, "factor": round(factor, 4),
                        "model": cfg["model"]["name"]}
    if src_info and (tw, th) == (src_info["width"], src_info["height"]):
        result["error"] = "目标分辨率与源相同（%dx%d），无需放大" % (tw, th)
        return result

    # Topaz 可用性
    tvai_ok = bool(tools.get("ffmpeg")) and not problems and probe_filter(tools)
    n_weights, sample_files = model_weight_files(tools, cfg["model"]["name"]) if tools.get("model_data_dir") else (0, [])
    result["model_weights"] = {"count": n_weights, "sample": sample_files}
    if tvai_ok and n_weights == 0:
        tvai_ok = False
        problems = list(problems) + [
            "模型 %s 的权重（*.tz3）不在 %s 里" % (cfg["model"]["name"],
                                            tools.get("model_data_dir") or "?")]
    result["topaz_available"] = tvai_ok
    result["problems"] = problems

    if verify_only:
        if not os.path.exists(dst):
            result["error"] = "产物不存在，无法只做验证：%s" % dst
            return result
        v = verify(tools, cfg, src, dst, tmpdir, do_sharpness=not fallback_lanczos)
        result["verify"] = v
        result["ok"] = v["passed"]
        return result

    if dry_run:
        cmd = build_cmd(cfg, tools, src, dst, tw, th)
        fps = float((cfg.get("perf") or {}).get("expected_fps") or 8.2)
        est = round(src_info["nb_frames"] / fps, 1) if src_info["nb_frames"] else None
        result["plan"] = {
            "command": " ".join('"%s"' % c if " " in c else c for c in cmd),
            "env": {"TVAI_MODEL_DIR": tools.get("model_dir"),
                    "TVAI_MODEL_DATA_DIR": tools.get("model_data_dir")},
            "estimated_seconds": est,
            "estimated_human": ("约 %.1f 分钟（按实测 %.1f 帧/秒估）" % (est / 60.0, fps)
                                if est else None),
            "estimated_size_mb": round(src_info["size"] * (tw * th) / float(
                max(src_info["width"] * src_info["height"], 1)) * 0.9 / 1048576.0, 1),
            "source": src_info,
        }
        result["ok"] = tvai_ok
        if not tvai_ok:
            result["hint"] = _hint(problems)
        return result

    if not tvai_ok and not fallback_lanczos:
        result["error"] = "Topaz 不可用，且没有开 --fallback-lanczos（拒绝用插值冒充高清）"
        result["hint"] = _hint(problems)
        return result

    key = cache_key(cfg, tools, src, tw, th)
    old = load_json(report_path, {}) or {}
    if (not force) and (not sample) and os.path.exists(dst) and old.get("cache_key") == key and old.get("ok"):
        result["ok"] = True
        result["cached"] = True
        result["verify"] = old.get("verify")
        result["elapsed_s"] = 0
        result["actions"].append("命中缓存（参数与源都没变），跳过重跑")
        return result

    os.makedirs(subs_dir, exist_ok=True)
    env = os.environ.copy()
    if tools.get("model_dir"):
        env["TVAI_MODEL_DIR"] = tools["model_dir"]
    if tools.get("model_data_dir"):
        env["TVAI_MODEL_DATA_DIR"] = tools["model_data_dir"]
    env.update(tools.get("extra_env") or {})

    total = src_info["nb_frames"] or 0
    if tvai_ok:
        cmd = build_cmd(cfg, tools, src, dst, tw, th)
        engine = "topaz:%s" % cfg["model"]["name"]
    else:
        cmd = [tools["ffprobe"], "-version"] if False else [
            "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", src,
            "-vf", "scale=w=%d:h=%d:flags=lanczos" % (tw, th),
            "-c:v", cfg["encode"]["codec"], "-pix_fmt", "yuv420p"]
        if cfg["encode"]["codec"] == "h264_nvenc":
            cmd += ["-preset", str(cfg["encode"].get("preset", "p5")),
                    "-rc", "constqp", "-qp", str(cfg["encode"].get("qp", 18)),
                    "-profile:v", "high"]
        cmd += ["-map", "0:v:0", "-map", "0:a?", "-c:a", "copy",
                "-fps_mode:v", "passthrough", "-movflags", "+faststart", dst]
        engine = "lanczos(插值，非 AI)"
    result["engine"] = engine
    result["actions"].append("开始放大：%s -> %dx%d（%s）" % (
        "%dx%d" % (src_info["width"], src_info["height"]), tw, th, engine))
    if not tvai_ok:
        result["warning"] = ("Topaz 不可用，本次用 lanczos 插值把分辨率提上去 —— "
                             "**画质并不会真的变好**，只是分辨率数字变大。原因：%s"
                             % "；".join(problems))

    if total and not quiet:
        say("   放大 %s：%dx%d -> %dx%d ｜ %d 帧 ｜ 模型 %s"
            % (ep, src_info["width"], src_info["height"], tw, th, total, engine))
    rc, tail, elapsed = run_ffmpeg(cmd, env, log_path, total, quiet=quiet)
    result["elapsed_s"] = elapsed
    result["ffmpeg_rc"] = rc
    result["avg_fps"] = round(total / elapsed, 2) if elapsed > 0 and total else None
    if rc != 0 or not os.path.exists(dst):
        result["error"] = "ffmpeg 失败 rc=%s" % rc
        result["tail"] = tail
        result["hint"] = _hint(problems) if problems else ""
        return result

    v = verify(tools, cfg, src, dst, tmpdir, do_sharpness=tvai_ok)
    result["verify"] = v
    result["ok"] = bool(v["passed"])
    if not result["ok"] and not allow_flat:
        result["error"] = "产物自检未全部通过（见 verify.checks）"
    result["cache_key"] = key
    _cleanup(tmpdir)
    _cleanup(tmpdir + "_src")
    if sample_src and os.path.exists(sample_src):
        # 样片源留着没意义（下一条命令就是另一套参数），但报告里保留它是哪一段
        os.remove(sample_src)
    # 测速模式下额外给出「整集 / 30·60·90 分钟剧」的外推，省得每次口算
    if sample and result.get("avg_fps"):
        fps = result["avg_fps"]
        src_full = video_info(tools, os.path.join(subs_dir, "%s_master.mp4" % ep))
        frames_full = src_full["nb_frames"]
        result["extrapolation"] = {
            "fps": fps,
            "full_frames": frames_full,
            "episode_minutes": round(frames_full / fps / 60.0, 2),
            "per_video_minute_minutes": round(60.0 * 24 / fps / 60.0, 3),
            "drama_30min_hours": round(30 * 60 * 24 / fps / 3600.0, 2),
            "drama_60min_hours": round(60 * 60 * 24 / fps / 3600.0, 2),
            "drama_90min_hours": round(90 * 60 * 24 / fps / 3600.0, 2),
        }
    dump_json(report_path, result)
    return result


def _cleanup(d):
    """只删自己建的临时目录里的文件（本项目有过「通配符删错交付物」的事故，这里写死路径）。"""
    try:
        for n in os.listdir(d):
            p = os.path.join(d, n)
            if os.path.isfile(p):
                os.remove(p)
        os.rmdir(d)
    except OSError:
        pass


def _hint(problems):
    if not problems:
        return ""
    return ("\n".join("  - %s" % p for p in problems) +
            "\n  修法：① 打开一次 Topaz Video 图形界面，用该模型跑一次导出（权重会自动下载）；"
            "\n        ② 或在 video-pipeline/config.upscale.json 的 tool.install_dir / "
            "tool.model_dir / tool.model_data_dir 里写死路径。")


def main():
    harden_stdio()
    ap = argparse.ArgumentParser(description="高清放大（Topaz Video 本地 AI 超分）")
    ap.add_argument("--ep", required=True, help="集号，如 ep03")
    ap.add_argument("--workspace", default=None, help="项目根目录；也可用 DRAMA_WORKSPACE")
    ap.add_argument("--config", default=None, help="自定义配置文件路径")
    ap.add_argument("--target", default=None, help="覆盖目标分辨率，如 2560x1440 或 3840x2160")
    ap.add_argument("--factor", type=float, default=None, help="按倍数放大，如 2")
    ap.add_argument("--model", default=None, help="覆盖模型，如 prob-4 / iris-3 / rhea-1")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要执行的命令")
    ap.add_argument("--verify-only", action="store_true", help="只对已有产物做断言")
    ap.add_argument("--sample", type=float, default=0.0, metavar="秒",
                    help="测速样片：只用母版前 N 秒（产物/报告都另存，不碰正式交付物）")
    ap.add_argument("--out", default=None, help="自定义输出路径（配合 --sample 用）")
    ap.add_argument("--force", action="store_true", help="忽略缓存重跑")
    ap.add_argument("--fallback-lanczos", action="store_true",
                    help="Topaz 不可用时退到插值放大（画质不提升，会明确告警）")
    ap.add_argument("--allow-flat", action="store_true", help="自检未过也保留产物")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    from pathlib import Path
    if args.workspace:
        ws = str(Path(args.workspace).expanduser().resolve())
    elif WS is not None:
        ws = str(WS)
    else:
        raise SystemExit(WS_HINT or "找不到短剧工作区（用 --workspace 指定）")
    cfg = load_config(ws, args.config)
    if args.model:
        cfg["model"]["name"] = args.model
    if args.target:
        m = re.match(r"^\s*(\d+)\s*[xX*]\s*(\d+)\s*$", args.target)
        if not m:
            raise SystemExit("--target 格式应为 宽x高，例如 2560x1440")
        cfg["target"] = deep_merge(cfg["target"], {"mode": "exact",
                                                  "width": int(m.group(1)),
                                                  "height": int(m.group(2))})
    elif args.factor:
        cfg["target"] = deep_merge(cfg["target"], {"mode": "factor", "scale": args.factor})

    r = upscale_episode(ws, args.ep, cfg, force=args.force, dry_run=args.dry_run,
                        verify_only=args.verify_only,
                        fallback_lanczos=args.fallback_lanczos,
                        allow_flat=args.allow_flat, quiet=args.json,
                        sample=args.sample, out_path=args.out)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        _print_human(r)
    return 0 if r.get("ok") else 2


def _print_human(r):
    say("=" * 78)
    say("高清放大 · %s" % r["episode"])
    if r.get("error"):
        say("[FAIL] %s" % r["error"])
        if r.get("hint"):
            say(r["hint"])
        if r.get("tail"):
            say("--- ffmpeg 末尾输出 ---")
            say(r["tail"][-1200:])
        return
    t = r["target"]
    say("  源      %s%s" % (r["src"], "（前 %.1f 秒样片）" % r["sample_seconds"]
                            if r.get("sample_seconds") else ""))
    say("  目标    %dx%d（x%.3f）｜ 模型 %s ｜ 引擎 %s"
        % (t["width"], t["height"], t["factor"], t["model"], r.get("engine", "-")))
    ex = r.get("extrapolation")
    if ex:
        say("  外推    本集(%d 帧) 约 %.1f 分钟 ｜ 每 1 分钟视频约 %.2f 分钟"
            % (ex.get("full_frames") or 0, ex["episode_minutes"], ex["per_video_minute_minutes"]))
        say("          30/60/90 分钟剧：%.2f / %.2f / %.2f 小时"
            % (ex["drama_30min_hours"], ex["drama_60min_hours"], ex["drama_90min_hours"]))
    plan = r.get("plan")
    if plan:
        say("  预计    耗时 %s ｜ 体积 约 %.0f MB"
            % (plan.get("estimated_human") or "?", plan.get("estimated_size_mb") or 0))
        say("  环境    TVAI_MODEL_DIR=%s" % plan["env"].get("TVAI_MODEL_DIR"))
        say("          TVAI_MODEL_DATA_DIR=%s" % plan["env"].get("TVAI_MODEL_DATA_DIR"))
        say("  命令    %s" % plan["command"])
        say("  （--dry-run：什么都没跑。去掉这个参数才会真跑）")
    if r.get("cached"):
        say("  结果    命中缓存，跳过重跑")
    if r.get("elapsed_s"):
        say("  耗时    %.1fs ｜ 平均 %.2f 帧/秒" % (r["elapsed_s"], r.get("avg_fps") or 0))
    v = r.get("verify") or {}
    if v:
        say("  自检    %s" % ("全部通过" if v.get("passed") else "有未通过项"))
        for c in v.get("checks", []):
            say("    %s %s —— %s" % ("[OK]  " if c["ok"] else "[FAIL]", c["name"], c["detail"]))
    if r.get("warning"):
        say("  [WARN] %s" % r["warning"])
    d = v.get("dst") or {}
    if d:
        say("  产物    %s（%.1f MB，%d 帧，%.2fs）"
            % (r["dst"], d["size"] / 1048576.0, d["nb_frames"], d["duration"]))
    say("=" * 78)


if __name__ == "__main__":
    sys.exit(main())
