#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
字幕管线（可复用）—— 从剧本台词 + CTC 强制对齐时间，生成并烧录字幕。

用法（默认：对齐 → 生成 ASS → 无损拼接母版 → 烧录）：
    python scripts/subtitle.py --ep ep03
    python scripts/subtitle.py --ep ep03 --preview 2      # 只出前 2 段的预览，最快看到效果
    python scripts/subtitle.py --ep ep03 --stage ass      # 只生成 ASS，不渲染
    python scripts/subtitle.py --ep ep03 --soft           # 软字幕（不重编视频）
    python scripts/subtitle.py --ep ep03 --force-align    # 强制重跑对齐

★ 2026-09-21 起：**合并之后先做高清放大到 2K，再由用户决定要不要加字幕**。
    python scripts/subtitle.py --ep ep03               # 跑到 2K 母版就【停下】等你回答
    python scripts/subtitle.py --ep ep03 --burn        # 同一口气跑完（放大 → 烧字幕）
    python scripts/subtitle.py --ep ep03 --no-upscale  # 关掉放大（旧行为，1440p 直接烧）

设计要点（与《L3 字幕与 BGM 组装方案》一致）：
  * 时间轴【不】用提示词切点（实测 52% 偏差 >1s），一律用 CTC 强制对齐的实测时间
  * 样式/断行/位置全部来自 video-pipeline/config.subtitle.json，改配置不改代码
  * 语言参数化：zh 按字符断行（避头尾）；es 按单词边界断行（不切断单词）
  * 每步产物落盘、可缓存、可重跑；末尾有断言兜底（不信任 ffmpeg 退出码）
  * 【先放大、后烧字幕】：烧字幕必然重编码，若先烧后放，字幕会被 AI 超分"重绘"
    （描边发糊、笔画被当成纹理）。所以字幕必须烧在已经放大好的 2K 母版上。

产物目录：video-pipeline/output/subs/<ep>/
    align/<seg>.txt      字符级对齐原始输出
    cues.json            每集字幕条目（含来源与实测时间）
    <ep>.ass             成品字幕文件
    <ep>_master.mp4      无损拼接母版（无字幕，1440p）
    <ep>_master_2k.mp4   高清放大后的母版（无字幕，2K）★ 字幕烧在它上面
    <ep>_subbed.mp4      烧录字幕成片
    report.json          运行报告（可复核）
"""

import argparse
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time

# ---------------------------------------------------------------- 基础设施

def default_workspace():
    """默认工作区解析：环境变量 DRAMA_WORKSPACE > 脚本位置推断 > None。

    为什么不能只靠 `__file__` 推断：本脚本住在 **Skill 包**里时，推出来的是 Skill 目录，
    不是用户项目 —— 会报「找不到配置 <skill>/video-pipeline/config.subtitle.json」。
    ⇒ 调用方用 `--workspace "<项目根>"` 或设 `DRAMA_WORKSPACE` 指过去。

    刻意**不在 import 期退出**（否则连 `--help` 都跑不了）：解析不到返回 None，
    由 main() 给出可照抄的报错。
    """
    env = os.environ.get("DRAMA_WORKSPACE")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    cand = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(cand, "video-pipeline", "data")):
        return cand
    return None


ROOT = default_workspace()


def harden_stdio():
    """Windows 控制台是 GBK，直接打印非 GBK 字符会崩（本项目已踩过多次）。"""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def run(cmd, env=None, cwd=None, check=True, quiet=True):
    e = os.environ.copy()
    if env:
        e.update(env)
    p = subprocess.run(cmd, env=e, cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    out = p.stdout.decode("utf-8", errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError("命令失败 rc=%s\n%s\n%s" % (p.returncode, " ".join(map(str, cmd)), out[-2000:]))
    return p.returncode, out


def esc_filter_path(p):
    """ffmpeg 滤镜里的绝对路径：必须『转义冒号 + 整值加单引号』（已实测，别无他法）。"""
    s = str(p).replace("\\", "/")
    s = re.sub(r"^([A-Za-z]):", r"\1\\:", s)
    return "filename='%s'" % s


def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def dump_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 配置与素材

def load_config(ws):
    p = os.path.join(ws, "video-pipeline", "config.subtitle.json")
    if not os.path.exists(p):
        raise SystemExit("找不到配置：%s" % p)
    return load_json(p)


def load_upscale(ws):
    """载入高清放大配置（同目录的 upscale.py + config.upscale.json）。

    为什么要它：合并后要先放大到 2K，字幕是在【放大后的母版】上烧的 ——
    所以字幕画布 = 放大后的分辨率，字号按百分比自动跟着变大（视觉比例不变）。
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import upscale as _up
    except BaseException as e:            # ★ SystemExit 也要接住（_ws 解析不到时会抛它）
        return None, None, "高清放大引擎不可用（%s）" % e
    try:
        return _up, _up.load_config(ws), None
    except BaseException as e:
        return _up, None, "高清放大配置读取失败（%s）" % e


def video_dims(ffprobe, path):
    """取视频流的真实宽高（放大目标要按它算，不能拿配置里的画布冒充）。"""
    _, out = run([ffprobe, "-v", "error", "-select_streams", "v:0",
                  "-show_entries", "stream=width,height", "-of", "csv=p=0", path])
    nums = re.findall(r"\d+", out)
    if len(nums) < 2:
        raise SystemExit("读不到视频宽高：%s" % path)
    return int(nums[0]), int(nums[1])


def ffprobe_frames(ffprobe, path):
    _, out = run([ffprobe, "-v", "error", "-select_streams", "v:0",
                  "-count_frames", "-show_entries", "stream=nb_read_frames",
                  "-of", "csv=p=0", path])
    return int(out.strip().splitlines()[0])


def episode_segments(ws, ep):
    """返回该集的段列表（含成片路径与帧数），顺序 = shots.json 顺序。

    兼容两种产物结构（2026-10-07 扩）：
      A. output/<批次>/<段>/final.mp4          —— 旧单层结构
      B. output/<引擎目录>/<批次>/<段>/final.mp4 —— 《贤妻不装了》的 h3_free / h3_free_acc2 两层结构
    同一段在多处存在时取 **mtime 最新**（错脸返工重出后必须用新版本）。
    """
    shots = load_json(os.path.join(ws, "video-pipeline", "data", "shots.json"))
    ids = [s["shot_id"] for s in shots if s["shot_id"].startswith(ep + "_")]
    outdir = os.path.join(ws, "video-pipeline", "output")
    segs, missing = [], []
    for sid in ids:
        cands = [f for f in glob.glob(os.path.join(outdir, "*", "*", sid, "final.mp4"))
                 if os.path.getsize(f) > 2048]
        legacy = [f for f in glob.glob(os.path.join(outdir, "*", sid, "final.mp4"))
                  if os.path.getsize(f) > 2048]
        cands += legacy
        if cands:
            best = max(cands, key=os.path.getmtime)
            rel = os.path.relpath(os.path.dirname(best), outdir)
            segs.append({"shot_id": sid, "batch": rel, "file": best})
        else:
            missing.append(sid)
    return segs, missing


def dialogue_lines(ws, ep):
    """从 h3_prompts.json 取该集每段的 <d> 台词（按顺序）。"""
    prompts = load_json(os.path.join(ws, "video-pipeline", "data", "h3_prompts.json"))
    pat = re.compile(r"<d>\[[^\]]*\]\s*([^<]*)</d>")
    out = {}
    for sid, v in prompts.items():
        if not sid.startswith(ep + "_"):
            continue
        out[sid] = [m.group(1).strip() for m in pat.finditer(v.get("prompt", ""))]
    return out


# ---------------------------------------------------------------- 阶段 1：对齐

def align_in_window(cfg_tools, full, win_path, txt_path, out_txt, cursor, text, iso, extra,
                    win_end=None):
    """在 [cursor, 段尾] 窗口内对齐 text。

    ★ 与旧版的唯一区别：**失败不抛异常**，而是返回 (None, 原因)。
    为什么必须这样：实测 ep06_seg05 第 3 句（30 字 / 2.4s ≈ 12.5 字/秒）会让
    CTC 对齐器内部断言失败（`AssertionError: n != i`）。旧实现里 `run()` 直接抛
    RuntimeError ⇒ **整集字幕全部做不出来**。语速快是内容属性，不该炸管线。
    """
    # ★ 2026-10-07：不再预删窗口 tmp —— 宿主 safe-delete 钩子按【会话轮次】累计
    #   删除数（阈值 50），批量烧录必触发 fail-closed。ffmpeg -y / open("w") 都是
    #   覆盖写，不删旧文件也正确；残留由批量脚本收尾统一清理。
    for p in (win_path, out_txt):
        pass
    cmd = [cfg_tools["ffmpeg"], "-hide_banner", "-nostats", "-v", "error", "-y",
           "-i", full, "-ss", "%.3f" % cursor]
    if win_end is not None:
        # ★ 收紧窗口：CTC 在「窗口远长于台词真实跨度」时会自由乱放
        cmd += ["-t", "%.3f" % max(win_end - cursor, 0.2)]
    cmd += ["-ac", "1", "-c:a", "pcm_s16le", win_path]
    run(cmd)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        run([cfg_tools["aligner"], "--audio_path", win_path, "--text_path", txt_path,
             "--language", iso] + extra, env=cfg_tools.get("aligner_env"))
    except RuntimeError as e:
        msg = str(e)
        hits = re.findall(r"(?:AssertionError|RuntimeError|\w*Error):.*", msg)
        return None, (hits[-1].strip()[:120] if hits else "对齐器 rc!=0")
    if not os.path.exists(out_txt):
        return None, "对齐器无输出"
    rel = parse_align(out_txt)
    if not rel:
        return None, "对齐输出为空"
    return rel, None


def split_pieces(text, cfg, limit=12):
    """把一句台词按标点切成有序片段（标点跟随前一片）；无标点且过长则按字符对半切。

    用途：整句一次对齐失败时的**保真降级**——切小后逐片顺序对齐，仍然拿到真实
    CTC 时间轴，而不是直接放弃估算。片段越小，CTC 越容易收敛。
    """
    punct = cfg.get("break_punct", "")
    text = (text or "").strip()
    if len(text) <= 1:
        return [text]
    parts, cur = [], ""
    for ch in text:
        cur += ch
        if ch in punct:
            parts.append(cur)
            cur = ""
    if cur:
        parts.append(cur)
    if len(parts) > 1:
        return parts
    if len(text) > limit:
        mid = len(text) // 2
        return [text[:mid], text[mid:]]
    return [text]


def align_text(cfg_tools, full, tmp, sid, i, text, tag, langcfg, iso, extra,
               cursor, dur, depth=0, problems=None, win_end=None):
    """把一段文本对齐到 [cursor, win_end]（win_end 默认 = 段尾），返回 [(s, e, text), ...]。

    实测依据（2026-09-20 隔离实验，ep06_seg05 第 3 句，30 字 / 2.44s）：
      * 整句 + 2.44s 窗口           → 失败（`<star> != n` / `targets length is too long for CTC`）
      * 整句 + 9.4s 全窗口          → **成功 7.06–9.38**（whisper 真值 7.0–9.4，吻合）
      * 按标点切片 + 2.44s 窗口      → **四片全成功**：0.08–0.32 / 0.38–1.26 / 1.22–1.72 / 1.74–2.40
    ⇒ 结论：CTC 需要「帧数 ≥ 目标 token 数」，罗马化后每个汉字≈4–5 token，
      所以**短窗口装不下长句，但装得下短句**。这不是对齐错位，是表示法开销。

    `win_end` 是「策略」参数：默认段尾（宽窗口）。收窄它可以抑制 CTC 的乱放
    （实测 ep10_seg05 第 2 句在 6.6s 窗口里被放到 9.56–12.12，真值 5.68–6.88）。
    """
    problems = problems if problems is not None else []
    if not text:
        return []
    limit = dur if win_end is None else min(win_end, dur)
    if cursor >= limit - 0.05:
        problems.append({"i": i, "text": text[:16], "reason": "音频已到窗口/尾部，无音频可对齐"})
        return []
    win = os.path.join(tmp, "%s_%s_%d.wav" % (sid, tag, depth))
    inp = os.path.join(tmp, "%s_%s_%d.txt" % (sid, tag, depth))
    out_txt = os.path.splitext(win)[0] + ".txt"
    rel, err = align_in_window(cfg_tools, full, win, inp, out_txt, cursor, text, iso, extra,
                               win_end=win_end)
    if rel is not None:
        s = cursor + rel[0][0]
        e = cursor + rel[-1][1]
        if e <= s:
            e = s + 0.4
        return [(round(s, 3), round(e, 3), text)]
    if depth < 2:
        parts = split_pieces(text, langcfg, limit=12 if depth == 0 else 6)
        if len(parts) > 1:
            out = []
            for k, p in enumerate(parts):
                out += align_text(cfg_tools, full, tmp, sid, i, p, "%s%d" % (tag, k),
                                  langcfg, iso, extra, cursor, dur, depth + 1, problems, win_end)
            if out:
                return out
    problems.append({"i": i, "text": text[:16], "reason": err or "对齐失败"})
    return []


def align_line(cfg_tools, full, tmp, sid, i, text, langcfg, iso, extra, dur, cursor,
               cps_floor=None):
    """顺序约束地对齐**一句**台词，返回 (record, new_cursor, problems)。

    降级链（越靠后越不保真，但绝不让整集失败）：
      ① 整句在 [cursor, 窗口尾] 内对齐       —— 正常路径（ep03 全靠这条）
      ② 按标点切片、各片在**同一窗口**内对齐 —— 快语速长句的保真降级（实测有效）
      ③ 按剩余时长估算                      —— 最后手段，写进 problems 留痕

    `cps_floor`（字/秒）> 0 时启用「紧窗口」策略：把窗口尾收到
    `cursor + max(字数/cps_floor, 1.2) + 0.6`，用来抑制 CTC 的远距离乱放。
    """
    problems = []
    win_end = None
    if cps_floor:
        n = max(spoken_len(text), 1)
        win_end = min(dur, cursor + max(n / float(cps_floor), 1.2) + 0.6)
    spans = align_text(cfg_tools, full, tmp, sid, i, text, "w", langcfg, iso, extra,
                       cursor, dur, 0, problems, win_end)
    if spans:
        # 各片是同一窗口内的独立对齐 ⇒ 可能轻微交叠，这里强制单调不重叠
        fixed, last = [], cursor
        for s, e, t in spans:
            s = max(s, last)
            if e <= s:
                e = s + 0.4
            fixed.append((round(s, 3), round(e, 3), t))
            last = e
        spans = fixed
        s, e = spans[0][0], spans[-1][1]
        source = "ctc-sequential" if len(spans) == 1 else "ctc-piecewise"
        cursor = e
    else:
        remain = max(dur - cursor, 0.0)
        s = round(min(cursor, dur), 3)
        e = round(min(max(cursor + remain, s + 0.4), dur), 3)
        if e <= s:
            # 音频已用尽：宁可退到段尾内侧 0.4s，也绝不越过段尾（越界会盖住下一段）
            e = round(dur, 3)
            s = round(max(dur - 0.4, 0.0), 3)
        source = "fallback-estimate"
        problems.append({"i": i, "text": text[:16],
                         "reason": "整句与切片对齐均失败，按剩余时长估算"})
        cursor = e
    rec = {"i": i, "text": text, "start": round(s, 3), "end": round(e, 3),
           "source": source}
    if len(spans) > 1:
        rec["pieces"] = [{"text": p[2], "start": p[0], "end": p[1]} for p in spans]
    return rec, cursor, problems


def stage_align(ws, ep, cfg, segs, lines, subs_dir, force=False):
    """逐段做【顺序约束的逐条对齐】，产出 align/<seg>.json。

    ★ 繁体中文（2026-10-07）：traditional_zh=true 时，**对齐仍吃简体原文**——
      CTC 对齐器词典不含繁体字形，喂繁体整段断言失败（实测 <star> != 湯，
      ep01_seg02 全句降级 fallback）。繁体转换在 align 产物【落盘时】做：
      dump 后立即把 lines[].text / problems[].text 做 s2tw 写回 ——
      后续 ASS/cues 从本文件读文本，拿到的即繁体终稿。

    ⚠ 为什么不"整段文本一次性对齐"（踩过的坑）：
       把一段的全部台词一起喂给 CTC，对齐器会把文本在整段音频上重新分配。
       当音频里某处存在与首句声学相似的片段时，首句会被"吸引"过去 ——
       实测 seg05 首句被推到 3.72s（真值 0.08s）、seg06 首句被推到 4.42s（真值 0.20s）。
       隔离实验证明：单独对齐首句时结果完全正确（0.08-1.94）。

    ⚠ 为什么不是"简单逐条对齐"：
       每条都在整段音频上找最佳位置，会互相抢占同一区间（实测 seg07 两行区间重叠）。

    ⇒ 正确做法：逐条对齐 + 【前一条的终点作为下一条的搜索窗口起点】（顺序单调约束）。
       实测：修好 seg05/seg06，且不破坏本来就正确的 seg04（2.52-7.40）。

    ⚠ 2026-09-20 追加：**顺序约束还不够** —— 窗口本身可能远长于台词真实跨度。
       实测 ep10_seg05 第 2 句：窗口 [5.60, 12.22]（6.6s）里被放到 **9.56–12.12**，
       而 whisper 真值是 **5.68–6.88**；结果把剩余音频吃光，第 3/4 句只能估算
       （26 字 / 0.10s = 250 字/秒）。同一病根也会造成**段首首句被推后**。
       ⇒ 采用【双策略 + 声学覆盖度择优】：
          S1「宽窗口」= [cursor, 段尾]（= 既有行为，多数段最优）
          S2「紧窗口」= [cursor, cursor + max(字数/3.5, 1.2) + 0.6]
        用缓存的 whisper 语音区间算「有语音却无字幕」的总时长作为**分数**，
        S2 必须比 S1 好 0.5 秒以上才被采用 —— 保证不牺牲既有正确结果。
    """

    cfg_tools = cfg["tools"]
    lang = cfg["default_language"]
    iso = cfg_tools["aligner_language_code"].get(lang, "cmn")
    adir = os.path.join(subs_dir, "align")
    tmp = os.path.join(subs_dir, "_tmp")
    asr_dir = os.path.join(subs_dir, "asr")
    os.makedirs(adir, exist_ok=True)
    os.makedirs(tmp, exist_ok=True)
    os.makedirs(asr_dir, exist_ok=True)
    extra = cfg_tools.get("aligner_args", ["--split_size", "char", "--romanize"])
    langcfg = cfg["languages"][lang]   # ★ 断句规则在语言段里，别把顶层 config 传下去
    IMPOSS = langcfg.get("cps_impossible", 20.0)
    # 窗口策略：宽 → 紧 → 更紧。按顺序择优，后一档必须比当前最好再**好 0.5 秒**才被采用，
    # 这样既能修「乱放」，又不会牺牲本来对齐正确的段（实测多数段 wide 已是最优）。
    STRATEGIES = [("wide", None), ("tight", 3.5), ("tighter", 6.0)]

    _PUNCT = re.compile(r"[，。！？；：…—、（）「」《》\s\"'!?.,;:\-]")

    def parse_srt(p):
        """解析 whisper 切片转写 srt → [(start, end, text)]；缺失/损坏返回 []。"""
        try:
            txt = open(p, encoding="utf-8-sig", errors="replace").read()
        except OSError:
            return []
        out = []
        for blk in re.split(r"\n\s*\n", txt.strip()):
            ls = [x for x in blk.splitlines() if x.strip()]
            if len(ls) < 3:
                continue
            m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)",
                         ls[1])
            if not m:
                continue
            g = [int(x) for x in m.groups()]
            out.append((g[0]*3600 + g[1]*60 + g[2] + g[3]/1000.0,
                        g[4]*3600 + g[5]*60 + g[6] + g[7]/1000.0,
                        " ".join(ls[2:])))
        return out

    def asr_calibrate(txts, srt):
        """★ S3 whisper 校准（2026-10-07 加）：切片 srt 与台词按序字符映射。

        为什么可信：whisper 切片转写的【时间】是实测真值（SKILL.md 口径：
        整段时间戳会被音乐拉长，切片后单独转写才可信）。文本只用来做单调
        匹配——繁体台词 vs whisper 简体转写在比较前统一 OpenCC tw2s 归一。
        全部台词都匹配成功（字符重叠 >=0.5、顺序单调）才返回 recs，否则
        None（让位给 CTC 档位）。
        """
        try:
            from opencc import OpenCC
            cc = OpenCC("tw2s")
        except ImportError:
            cc = None

        def norm(s):
            s = cc.convert(s) if cc else s
            return _PUNCT.sub("", s)

        srt_n = [(a, b, norm(x)) for a, b, x in srt]
        pairs, j = [], 0
        for t in txts:
            core = norm(t)
            if not core:
                return None
            # 顺序贪心：在 [j:] 里找与该台词字符重叠率最高的 srt 条。
            # 台词分句与 whisper 转写分句常常不对齐（ep14_seg05：台词 1 句跨
            # 转写 2 条），所以找到主匹配条后还要【向后吞并】仍有字符重叠的
            # 连续条，时间取并入区间并集。
            best, bi = 0.0, None
            for k in range(j, len(srt_n)):
                inter = len(set(core) & set(srt_n[k][2]))
                den = min(len(core), len(srt_n[k][2])) or 1
                sc = inter / den
                if sc > best:
                    best, bi = sc, k
            if best < 0.3 or bi is None:
                return None
            e = bi
            while e + 1 < len(srt_n) and (set(core) & set(srt_n[e + 1][2])):
                e += 1
            pairs.append((srt_n[bi][0], srt_n[e][1], t))
            j = e + 1
        fixed, last = [], 0.0
        for a, b, t in pairs:
            a = max(a, last)
            # ★ 最小时长保底（2026-10-07）：whisper 对短句的 end-start 偏窄
            #   （实测「是一隻鐲子。」5 字仅 0.24s → 21 字/秒撞语速生理上限断言，
            #   ep69 整集失败）。按发音字数给保底：0.28s/字、至少 0.6s；
            #   拉长后与下一条的冲突由下方顺序单调（a=max(a,last)）自然消化。
            core_len = len(_PUNCT.sub("", t))
            b = max(b, a + max(0.6, 0.28 * core_len))
            fixed.append({"text": t, "start": round(a, 3), "end": round(b, 3),
                          "source": "whisper-asr"})
            last = b
        return fixed

    def run_strategy(seg, sid, full, dur, txts, cps_floor):
        recs, cursor, bad = [], 0.0, []
        for i, t in enumerate(txts):
            rec, cursor, probs = align_line(cfg_tools, full, tmp, sid, i, t,
                                            langcfg, iso, extra, dur, cursor, cps_floor)
            recs.append(rec)
            bad.extend(probs)
        # ★ 打分：这段音频里「有语音却没有字幕覆盖」的总时长（越小越好）
        gaps = coverage_gaps(cfg, seg["file"],
                             [(r["start"], r["end"]) for r in recs],
                             asr_dir, sid, min_gap=0.5)
        score = sum(g[2] for g in gaps)
        # 生理上限之外的语速是硬错误，额外重罚
        for r in recs:
            d = max(r["end"] - r["start"], 1e-6)
            if spoken_len(r["text"]) / d > IMPOSS:
                score += 5.0
        return score, recs, bad

    done, skipped = 0, 0
    for seg in segs:
        sid = seg["shot_id"]
        dst = os.path.join(adir, sid + ".json")
        txts = lines.get(sid) or []
        if not txts:
            continue
        if os.path.exists(dst) and not force:
            skipped += 1
            continue
        full = os.path.join(tmp, sid + "_full.wav")
        run([cfg_tools["ffmpeg"], "-hide_banner", "-nostats", "-v", "error", "-y",
             "-i", seg["file"], "-vn", "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", full])
        dur = audio_duration(cfg_tools["ffprobe"], full)
        cand = []
        for sname, floor in STRATEGIES:
            sc, rc, bd = run_strategy(seg, sid, full, dur, txts, floor)
            cand.append((sname, sc, rc, bd))
        # ★ S3 whisper 校准（2026-10-07）：asr/<sid>.srt 与台词按序一一映射时，
        #   whisper 切片时间就是真值——ep01_seg02 实测 CTC 把后 3 句早放 4.7-7s，
        #   而 srt 与语音完全吻合。S3 构造后与 CTC 档位同台竞争（同一覆盖分口径，
        #   低于当前最优 0.5s 以上才顶替——规则与既有档位一致）。
        srt_entries = parse_srt(os.path.join(asr_dir, sid + ".srt"))
        if srt_entries:
            cal = asr_calibrate(txts, srt_entries)
            if cal:
                gaps3 = coverage_gaps(cfg, seg["file"],
                                      [(r["start"], r["end"]) for r in cal],
                                      asr_dir, sid, min_gap=0.5)
                cand.append(("asr-whisper", sum(g[2] for g in gaps3), cal, []))
        # 默认取 wide；后续档位要「好 0.5 秒以上」才顶替
        strat, score, recs, bad = cand[0]
        for sname, sc, rc, bd in cand[1:]:
            if sc < score - 0.5:
                strat, score, recs, bad = sname, sc, rc, bd
        scores = {c[0]: round(c[1], 2) for c in cand}

        # ★ 单条 whisper 校正（2026-10-07）：S3 全匹配失败回退 CTC 后，仍可能有
        #   个别句被压进极短区间（cps > 生理上限）。此时用 asr srt 里与该句
        #   字符重叠最高的条目时间替换——覆盖检查已证明那才是真区间
        #   （ep14_seg05 实测：14 字被压进 0.27s，真区间 2.96-4.48s 空着）。
        if srt_entries:
            for _r in recs:
                _d = max(_r["end"] - _r["start"], 1e-6)
                # 触发条件二选一：① 语速超生理上限（对齐错位铁证）
                #                ② 时长 < 0.3s 硬底线（ep81「誰？」单字句 0.08s 实测）
                if spoken_len(_r["text"]) / _d > IMPOSS or _d < 0.3:
                    _cal = asr_calibrate([_r["text"]], srt_entries)
                    if _cal:
                        _r["start"], _r["end"] = _cal[0]["start"], _cal[0]["end"]
                        _r["source"] = "whisper-asr"
                        _r.pop("pieces", None)
                        print("   ⚠ %s 句%d 异常区间 → whisper 校正至 %.2f-%.2f"
                              % (sid, _r["i"], _r["start"], _r["end"]))
        dump_json(dst, {"shot_id": sid, "audio_duration": round(dur, 3),
                        "method": strat, "strategy": strat,
                        "gap_score": scores,
                        "lines": recs, "problems": bad})

        # ★ 繁体终稿（2026-10-07）：对齐吃的是简体原文（CTC 词典不含繁体字形，
        #   喂繁体整段 AssertionError），所以转换放在【落盘时】——
        #   把本文件 lines[].text / problems[].text 做 s2tw 写回，
        #   后续 ASS/cues 阶段从本文件读文本，拿到的即繁体。
        if cfg.get("traditional_zh"):
            try:
                from opencc import OpenCC
                _cc = OpenCC(str(cfg.get("traditional_profile") or "s2tw"))
                _d = load_json(dst)
                for _r in _d.get("lines", []):
                    if _r.get("text"):
                        _r["text"] = _cc.convert(_r["text"])
                for _p in _d.get("problems", []):
                    if _p.get("text"):
                        _p["text"] = _cc.convert(_p["text"])
                dump_json(dst, _d)
            except ImportError:
                print("   ★ 警告：traditional_zh=true 但未安装 opencc-python-reimplemented，回退简体")
        s1, s2 = scores["wide"], scores["tight"]
        if bad or strat == "tight":
            note = "；".join(sorted({b["reason"] for b in bad}))[:100] if bad else "覆盖率更优"
            print("   ⚠ %s：策略=%s（缺口分 wide %.2fs / tight %.2fs）｜%d 处降级 %s"
                  % (sid, strat, s1, s2, len(bad), note))
        done += 1
    # ★ 2026-10-07：不再 rmtree _tmp —— 本环境的安全删除钩子对【进程内累积删除】
    #   超 50 个文件强制 fail-closed（SAFE_DELETE_BULK_CONFIRM_REQUIRED），批量烧录
    #   跑到几十集必炸（ep98 实录 count=216）。临时 wav 每集 ~3-8 个、单个 <1MB，
    #   留在原地，由批量脚本/人工在跑完后统一清理。
    # shutil.rmtree(tmp, ignore_errors=True)
    return done, skipped


def audio_duration(ffprobe, path):
    _, out = run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                  "-of", "csv=p=0", path])
    return float(out.strip().splitlines()[0])


def spoken_len(text):
    """只统计【会发音的字】，不含标点——「真的？！」实际只说 2 个字，不是 4 个。
    （踩过的坑：把标点算进去会让语速被高估，误报成"对齐错位"）"""
    return len(re.sub(r"[！？。，、；：…—「」《》（）\s\"'!?.,;:\-]", "", text))


def speech_regions(cfg, path, asr_dir=None, tag=None, noise_db=-35, min_sil=0.25):
    """取该段的【真实语音区间】。

    优先用 whisper-cli 的转写结果 —— 它只输出识别到「词」的地方，
    背景音乐/音效不会被算进去。silencedetect 会把音乐当语音（踩过的坑：
    seg14 前 9.66 秒 whisper 什么都没听到，silencedetect 却判为"有语音"）。
    没有 whisper-cli 时退回 silencedetect。
    """
    wc = cfg["tools"].get("whisper_cli")
    model = cfg["tools"].get("whisper_model")
    if wc and model and os.path.exists(wc) and os.path.exists(model):
        base = os.path.join(asr_dir or os.path.dirname(path), tag or "asr")
        srt = base + ".srt"
        wav = base + ".wav"
        # whisper-cli 只接受 16k 单声道 WAV；直接喂 mp4 会静默失败（踩过的坑）
        if not os.path.exists(wav):
            run([cfg["tools"]["ffmpeg"], "-hide_banner", "-nostats", "-v", "error", "-y",
                 "-i", path, "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav])
        if not os.path.exists(srt):
            run([wc, "-m", model, "-f", wav, "-l", cfg["tools"].get("whisper_lang", "zh"),
                 "-osrt", "-of", base, "-np"], check=False)
        regs = []
        if os.path.exists(srt):
            txt = open(srt, encoding="utf-8", errors="replace").read().replace("\r", "")
            for blk in re.split(r"\n\s*\n", txt.strip()):
                L = [x for x in blk.split("\n") if x.strip()]
                if len(L) < 3:
                    continue
                m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", L[1])
                if not m:
                    continue
                f = lambda a, b, c, d: int(a) * 3600 + int(b) * 60 + int(c) + int(d) / 1000
                regs.append((f(*m.group(1, 2, 3, 4)), f(*m.group(5, 6, 7, 8))))
        if regs:
            return regs
    # 退回纯声学
    rc, out = run([cfg["tools"]["ffmpeg"], "-hide_banner", "-nostats", "-i", path,
                   "-vn", "-af", "silencedetect=noise=%ddB:d=%s" % (noise_db, min_sil),
                   "-f", "null", "NUL"], check=False)
    sil, cur = [], None
    for ln in out.splitlines():
        m = re.search(r"silence_start:\s*([\d.]+)", ln)
        if m:
            cur = float(m.group(1))
            continue
        m = re.search(r"silence_end:\s*([\d.]+)", ln)
        if m and cur is not None:
            sil.append((cur, float(m.group(1))))
            cur = None
    dur = audio_duration(cfg["tools"]["ffprobe"], path)
    if cur is not None:
        sil.append((cur, dur))
    sil.sort()
    reg, t = [], 0.0
    for a, b in sil:
        if a - t > 0.12:
            reg.append((t, a))
        t = max(t, b)
    if dur - t > 0.12:
        reg.append((t, dur))
    return reg


def coverage_gaps(cfg, path, subs, asr_dir=None, tag=None, min_gap=0.6):
    """找出【有语音但没有任何字幕覆盖】的区间 —— 这是"字幕没对齐/漏台词"的机器判据。
    实测：这个判据能抓住 seg05/seg06 首句被推后 3.7/4.2 秒的问题（语速判据抓不住）。"""
    gaps = []
    for (a, b) in speech_regions(cfg, path, asr_dir, tag):
        covered = [(max(a, s), min(b, e)) for s, e in subs if e > a and s < b]
        covered = [c for c in covered if c[1] > c[0]]
        if not covered:
            if b - a >= min_gap:
                gaps.append((round(a, 2), round(b, 2), round(b - a, 2)))
            continue
        covered.sort()
        cur = a
        for c0, c1 in covered:
            if c0 - cur >= min_gap:
                gaps.append((round(cur, 2), round(c0, 2), round(c0 - cur, 2)))
            cur = max(cur, c1)
        if b - cur >= min_gap:
            gaps.append((round(cur, 2), round(b, 2), round(b - cur, 2)))
    return gaps


def collect_gaps(segs, cues, seg_offsets, cfg, asr_dir):
    """逐段跑声学覆盖检查，返回缺口列表（字幕时间已按段起点换算成"集内相对时间"）。"""
    gaps = []
    for seg in segs:
        sid = seg["shot_id"]
        base = seg_offsets.get(sid, 0.0)
        rel_subs = [(c["start"] - base, c["end"] - base) for c in cues if c["shot_id"] == sid]
        for (a, b, d) in coverage_gaps(cfg, seg["file"], rel_subs, asr_dir, sid, min_gap=0.6):
            gaps.append({"shot_id": sid, "from": a, "to": b, "seconds": d})
    return gaps


def repair_leading_gaps(segs, cues, seg_offsets, gaps, warn, lead=0.06):
    """★ 段首覆盖修复：把「段首有语音却无字幕」的区间并入该段第一条字幕。

    实测依据（2026-09-20；真值取自 whisper-cli `-ml 1` 的**逐字**时间戳）：

      | 段          | 首句真实起点 | CTC 对齐起点 | 被推后 |
      |-------------|--------------|--------------|--------|
      | ep06_seg01  | 0.00         | 1.84         | +1.84s |
      | ep06_seg02  | 0.00         | 5.44         | +5.44s |
      | ep06_seg07  | 0.00         | 3.96         | +3.96s |

    成因：CTC 的搜索窗口是 [cursor, 段尾]；对**第一句**来说窗口远长于台词真实跨度，
    模型会自行挑一段子区间。后续各行因顺序约束把窗口收紧了，所以不受影响 ——
    这解释了为什么"只有段首错、后面都对"。
    ⇒ 观众看到的现象：前几秒**听得到人声却没有字幕**。
    这里**只外扩时间**（不改文本、不改行序），把段首人声盖住。
    """
    targets = {}
    for g in gaps:
        if g["from"] <= 0.05 and g["seconds"] >= 0.3:
            targets[g["shot_id"]] = min(targets.get(g["shot_id"], 9e9), g["from"])
    if not targets:
        return 0
    fixed = []
    for sid, gap_from in targets.items():
        base = seg_offsets.get(sid, 0.0)
        seg_cues = [c for c in cues if c["shot_id"] == sid]
        if not seg_cues:
            continue
        first = min(seg_cues, key=lambda c: c["start"])
        want = round(max(base + max(gap_from - lead, 0.0), 0.0), 3)
        if first["start"] > want:
            first["start"] = want
            fixed.append(sid)
    if not fixed:
        return 0
    # 前移后必须重新保证单调不重叠、且不短于可读下限
    cues.sort(key=lambda c: c["start"])
    for i, c in enumerate(cues):
        nxt = cues[i + 1]["start"] if i + 1 < len(cues) else None
        if nxt is not None and c["end"] > nxt:
            c["end"] = nxt
        if c["end"] - c["start"] < 0.4:
            c["end"] = round(c["start"] + 0.4, 3)
    warn.append("段首覆盖修复：%d 段的第一条字幕已前移盖住段首人声（%s）"
                % (len(fixed), ", ".join(fixed)))
    return len(fixed)


def parse_align(path):
    """解析对齐输出：每行 `start-end: 字`，返回 [(start, end, char), ...]。"""
    ent = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            m = re.match(r"([\d.]+)-([\d.]+):\s?(.*)$", ln.rstrip("\n"))
            if m:
                ent.append((float(m.group(1)), float(m.group(2)), m.group(3)))
    return ent


# ---------------------------------------------------------------- 阶段 2：字幕条目

def split_two_lines(text, cfg):
    """把一条台词折成 ≤max_lines 行，每行 ≤max_line_chars；中文按字符、西语按单词。

    断行策略（实测踩过的坑）：
      * 中文没有空格，任何"按字符硬切"都可能把词切开（如「癌/症」）⇒ 优先在标点处断
      * 标点断点若略微超出行宽，允许 line_tolerance_chars 的容差 —— 宁可行长一点，
        也不要把词切开（实测：允许 +3 字容差后，「…怎么可能得癌症！」能整段落在一行）
    """
    ml = cfg["max_line_chars"]
    tol = cfg.get("line_tolerance_chars", 0)
    n = len(text)
    if n <= ml:
        return [text]
    if cfg.get("wrap") == "word":
        # 西语：必须按单词边界，绝不切断单词
        words = text.split(" ")
        for k in range(1, len(words)):
            left, right = " ".join(words[:k]), " ".join(words[k:])
            if len(left) <= ml and len(right) <= ml:
                return [left, right]
        return [text]
    # 中文：优先在标点处断（允许容差），取离中点最近者
    punct = cfg.get("break_punct", "")
    mid = n // 2
    best = None
    for i in range(1, n):
        if text[i - 1] in punct:
            if max(i, n - i) <= ml + tol:
                score = abs(i - mid)
                if best is None or score < best[0]:
                    best = (score, i)
    if best:
        return [text[:best[1]], text[best[1]:]]
    # 兜底：在容差范围内取最接近中点处硬切（仍可能切词，但没有标点可用）
    cut = min(ml + tol, n - 1)
    return [text[:cut], text[cut:]]


def split_long_cue(text, cfg):
    """整句超过 max_cue_chars 时，按标点拆成多条（因为一条字幕最多两行）。"""
    mc = cfg["max_cue_chars"]
    if len(text) <= mc:
        return [text]
    punct = cfg.get("break_punct", "")
    chunks, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= mc or (ch in punct and len(cur) >= mc * 0.55):
            chunks.append(cur)
            cur = ""
    if cur:
        if chunks and len(cur) < 6:
            chunks[-1] += cur
        else:
            chunks.append(cur)
    return chunks


def build_cues(ws, ep, cfg, segs, lines, subs_dir):
    """产出该集全部字幕条目（时间已换算成【整集绝对时间】）。"""
    lang = cfg["default_language"]
    lc = cfg["languages"][lang]
    fps = 24.0
    t0 = 0.0
    cues = []
    warn = []
    cps_hard, cps_soft = [], []
    seg_offsets = {}
    for seg in segs:
        sid = seg["shot_id"]
        seg_offsets[sid] = t0        # 记录该段在整集里的起点（覆盖检查要用）
        ap = os.path.join(subs_dir, "align", sid + ".json")
        if not os.path.exists(ap):
            warn.append("%s 没有对齐结果（先跑 --stage align）" % sid)
            continue
        rec = load_json(ap)
        # ★ 相邻台词连续性约束（2026-09-20 追加，实测依据见下）
        #   现象：CTC 有明确的【起点偏晚】倾向 —— 真值 0.00 被放到 1.84（ep06_seg01）、
        #         真值 0.00 被放到 5.44（ep06_seg02）、真值 3.0 被放到 5.76（ep06_seg06）、
        #         真值 5.68 被放到 7.86（ep10_seg05 第 2 句）。
        #   规律：本剧**同一段内相邻台词基本首尾相接**，实测间隔仅 0～0.2 秒 ——
        #         ep06_seg01 0-6.72 / 6.72-11.52；ep06_seg02 0-8.24 / 8.44-11.68；
        #         ep06_seg05 0-2.6 / 2.6-7.0 / 7.0-9.4；ep10_seg05 四句全连续。
        #   ⇒ 若本句起点落后上一句终点 **不超过 2.5 秒**，判定为「CTC 起点漂移」，
        #     把起点拉回上一句终点（只改起点，不动终点 ⇒ 只增覆盖、不减信息）。
        #   为什么不无条件拉：间隔 > 2.5s 说明中间是**剧本外语音**（音乐/他人插话），
        #     硬拉会把上一句的字幕盖到无关音频上 —— 实测 ep09_seg05 间隔 5.4s 即属此类。
        _prev_end = None
        for it in rec.get("lines", []):
            if _prev_end is not None:
                _gap = float(it["start"]) - _prev_end
                if 0.05 < _gap <= 2.5:
                    it["start"] = round(_prev_end, 3)
                    it["pulled_back"] = True
            _prev_end = float(it["end"])
        for it in rec.get("lines", []):
            t = it["text"]
            st, en = float(it["start"]), float(it["end"])
            if en <= st:
                en = st + lc["min_duration_s"]
            # ★ 语速合理性检查（对齐是否可信的机器判据）
            #   中文正常说话 3~5.5 字/秒；若某条对齐出 "8 字/秒"，那不是语速快，是【对齐错了】
            dur = en - st
            if dur > 0.05:
                cps = spoken_len(t) / dur          # ← 只算发音的字，不含标点
                lo, hi = lc.get("cps_ok", [0, 999])
                hard = lc.get("cps_hard_max", 999)
                imposs = lc.get("cps_impossible", hard * 1.7)
                crec = {"shot_id": sid, "text": t[:20], "dur": round(dur, 2),
                        "spoken": spoken_len(t), "cps": round(cps, 2),
                        "source": it.get("source", "")}
                if cps > imposs:
                    # ⚠️ 这里**只告警不致命**：此刻用的是【对齐原始记录】的时长，
                    #    而段首修复会把第一条字幕的起点前移（实测 ep06_seg06 原始 0.76s
                    #    → 修复后 3.94s，18.4 字/秒 是假象）。
                    #    真正的「生理上限」判定放在【最终字幕】上（见本函数末尾）。
                    cps_soft.append(crec)
                    warn.append("★ %s 「%s」对齐原始时长仅 %.2fs（%.1f 字/秒）——"
                                "若修复后仍如此则判定错位，见末尾的最终判定"
                                % (sid, t[:14], dur, cps))
                elif cps > hard:
                    # ★ 2026-09-20 修正：这条以前是【致命】断言，已被实测推翻。
                    #   证据：ep06_seg05 第 3 句 30 字 / 2.32s = 12.9 字/秒，whisper 独立转写
                    #   证实音频确实是这个语速（AI 配音把长台词压进短片长）。语速快是内容属性，
                    #   不是对齐错位的证据 ⇒ 降为告警，只有超过生理上限才算错位。
                    cps_soft.append(crec)
                    warn.append("⚠ %s 「%s」语速 %.1f 字/秒（%d 发音字 / %.2fs）很快 —— 已核对为真实语速则无需处理"
                                % (sid, t[:14], cps, spoken_len(t), dur))
                elif cps > hi or cps < lo:
                    cps_soft.append(crec)
                    warn.append("  %s 「%s」语速 %.1f 字/秒 超出常规区间 [%.1f, %.1f]"
                                % (sid, t[:14], cps, lo, hi))
            # 整句过长 → 拆多条，时间按字数比例分配
            parts = split_long_cue(t, lc)
            total_c = sum(len(p) for p in parts) or 1
            cur = st
            for p in parts:
                span = (en - st) * (len(p) / total_c)
                pst, pen = cur, min(cur + span, en)
                if pen - pst < lc["min_duration_s"]:
                    pen = min(pst + lc["min_duration_s"], en)
                cues.append({
                    "shot_id": sid,
                    "start": round(t0 + pst, 3),
                    "end": round(t0 + pen, 3),
                    "text": p,
                    "lines": split_two_lines(p, lc),
                    "source": it.get("source", "ctc-sequential")
                })
                cur = pen
        t0 += ffprobe_frames(cfg["tools"]["ffprobe"], seg["file"]) / fps
    # 排序 → 延长过短条目 → 去重叠（后一条不得早于前一条结束）
    #   注意：延长必须受【下一条起点】约束，否则会把两条字幕叠在一起
    cues.sort(key=lambda c: c["start"])
    total = t0
    for i, c in enumerate(cues):
        nxt = cues[i + 1]["start"] if i + 1 < len(cues) else total
        if c["end"] <= c["start"]:
            c["end"] = c["start"] + lc["min_duration_s"]
        if c["end"] - c["start"] < lc["min_duration_s"]:
            c["end"] = min(c["start"] + lc["min_duration_s"], nxt)
        if c["end"] > nxt:
            c["end"] = nxt
        if c["end"] - c["start"] < lc["min_duration_s"] - 1e-6:
            warn.append("%s 「%s」时长仅 %.2fs（< 目标 %.1fs，因下一句紧跟，无法延长）"
                        % (c["shot_id"], c["text"][:12], c["end"] - c["start"], lc["min_duration_s"]))

    # ★ 声学覆盖检查：段内【有语音却没有字幕覆盖】的区间 —— 这才是"字幕没对齐"的硬判据
    #   注意：必须减【该段在整集里的起点】，不是减"该段第一条字幕的起点"（踩过的坑：
    #   减错基准会让所有字幕整体前移，于是每段都误报一大堆缺口）
    asr_dir = os.path.join(subs_dir, "asr")
    os.makedirs(asr_dir, exist_ok=True)
    gaps = collect_gaps(segs, cues, seg_offsets, cfg, asr_dir)
    # ★ 先修「段首推后」，再复检 —— 报告里给出的是【修复后仍然存在】的缺口，不粉饰
    if repair_leading_gaps(segs, cues, seg_offsets, gaps, warn):
        gaps = collect_gaps(segs, cues, seg_offsets, cfg, asr_dir)
    warn.append("声学覆盖检查：%d 段中 %d 段存在『有语音但无字幕』的区间（合计 %.1fs）"
                % (len(segs), len({g["shot_id"] for g in gaps}),
                   sum(g["seconds"] for g in gaps)))
    for g in gaps:
        if g["seconds"] >= 1.5:
            warn.append("★ %s 集内 %.2f-%.2f s 有语音 %.2fs 却无字幕 ⇒ 对齐错位或台词缺失"
                        % (g["shot_id"], g["from"], g["to"], g["seconds"]))

    # ★ 生理上限的**最终判定**：判在【最终会渲染出去的字幕】上（含段首修复的效果）。
    #   为什么必须放这里：判在对齐原始记录上会误报（ep06_seg06 原始 0.76s → 修复后 3.94s）。
    imposs_final = lc.get("cps_impossible", 20.0)
    for c in cues:
        d = c["end"] - c["start"]
        if d > 0.05:
            v = spoken_len(c["text"]) / d
            if v > imposs_final:
                cps_hard.append({"shot_id": c["shot_id"], "text": c["text"][:20],
                                 "dur": round(d, 2), "spoken": spoken_len(c["text"]),
                                 "cps": round(v, 2), "source": c.get("source", ""),
                                 "final": True})
    for h in [x for x in cps_hard if x.get("final")]:
        warn.append("★ %s 「%s」最终字幕 %.1f 字/秒（%d 发音字 / %.2fs）超过生理上限"
                    % (h["shot_id"], h["text"][:14], h["cps"], h["spoken"], h["dur"]))
    return cues, warn, total, {"cps_hard": cps_hard, "cps_outliers": cps_soft,
                               "coverage_gaps": gaps}


# ---------------------------------------------------------------- 阶段 3：ASS

def ass_time(t):
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return "%d:%02d:%02d.%02d" % (h, m, s, cs)


def safe_area_limits(cfg):
    """平台字幕安全区下限（用户 2026-10-06 给定，属**硬性**规则）。

      · 距画面底部 **≥ 200px**
      · 文本框宽度 **≤ 视频宽度的 70%**

    为什么按【像素】存而不是百分比：平台口径就是像素。百分比会在换分辨率时漂移
    （200px 在 1080×1920 上是 10.4%，在 2560×1440 上只有 7.8%），所以配置里存像素，
    实现里取「配置百分比边距」与「安全区下限」的**较大值** ⇒ 任何画布下都满足。
    """
    sa = cfg.get("safe_area") or {}
    return {"min_bottom_px": float(sa.get("min_bottom_px") or 0),
            "max_text_width_pct": float(sa.get("max_text_width_pct") or 100)}


def style_metrics(cfg, lang, canvas):
    """按**画布**算出字号与边距，并叠加平台安全区（硬下限）。

    ★ 竖屏陷阱（实测）：旧配置按**高度**百分比定字号，对横屏 1440×832 是对的
      （6.7% × 832 = 56px），但对竖屏 1088×1920 会算出 129px —— 16 字/行 = 2064px，
      远超 70% 安全区允许的 760px。所以字号必须再受「文本框宽 ÷ 每行最多字符」反向约束。
    """
    lc = cfg["languages"][lang]
    W, H = canvas
    lim = safe_area_limits(cfg)
    margin_v = max(int(round(H * lc["margin_v_pct"] / 100.0)),
                   int(math.ceil(lim["min_bottom_px"])))
    need_lr = int(math.ceil(W * (100.0 - lim["max_text_width_pct"]) / 200.0))
    margin_lr = max(int(round(W * lc["margin_lr_pct"] / 100.0)), need_lr)
    usable_w = W - 2 * margin_lr
    font_h = int(round(H * lc["font_size_pct"] / 100.0))
    cw = float(lc.get("char_width_em", 1.0))
    font_fit = int(math.floor(usable_w / max(1.0, lc["max_line_chars"] * cw)))
    font_size = max(1, min(font_h, font_fit))
    return {"margin_v": margin_v, "margin_lr": margin_lr, "usable_w": usable_w,
            "text_w_pct": 100.0 * usable_w / W, "font_size": font_size,
            "font_size_by": "height" if font_size == font_h else "width-fit(fit 到安全区)",
            "limits": lim, "char_width_em": cw}


def qa_safe_area(ass_path, cfg):
    """★ 机器断言：从**写好的 ASS** 回读几何，验证平台字幕安全区。

    回读文件而不是复用算出来的值 —— 断言看产物、不看意图（本项目一贯做法）。
    这样以后谁改了写入逻辑，这道断言都会拦住。
    """
    with open(ass_path, encoding="utf-8", errors="replace") as fh:
        txt = fh.read()
    W, H, style = None, None, None
    for ln in txt.splitlines():
        if ln.startswith("PlayResX:"):
            W = int(ln.split(":", 1)[1].strip())
        elif ln.startswith("PlayResY:"):
            H = int(ln.split(":", 1)[1].strip())
        elif ln.startswith("Style: Default,"):
            style = ln
    if W is None or H is None or style is None:
        return {"pass": False, "failures": ["ASS 里找不到 PlayResX/Y 或 Default 样式行"]}
    p = style.split(",")
    font_size = int(p[2])                # p[0]="Style: Default", p[1]=字体名, p[2]=字号
    mL, mR, mV = int(p[-4]), int(p[-3]), int(p[-2])
    lim = safe_area_limits(cfg)
    lc = cfg["languages"][cfg.get("default_language", "zh")]
    text_w = W - mL - mR
    pct = 100.0 * text_w / W
    need_w = lc["max_line_chars"] * float(lc.get("char_width_em", 1.0)) * font_size
    fails = []
    if mV < lim["min_bottom_px"] - 0.5:
        fails.append("距底 %dpx < %dpx" % (mV, int(lim["min_bottom_px"])))
    if pct > lim["max_text_width_pct"] + 0.05:
        fails.append("文本宽 %.2f%% > %.0f%%" % (pct, lim["max_text_width_pct"]))
    if need_w > text_w + 0.5:
        fails.append("一行 %d 字 × %.2fem × %dpx = %.0fpx 溢出文本框 %dpx"
                     % (lc["max_line_chars"], float(lc.get("char_width_em", 1.0)),
                        font_size, need_w, text_w))
    return {"pass": not fails, "failures": fails,
            "canvas": "%dx%d" % (W, H), "font_size_px": font_size,
            "margin_v_px": mV, "margin_lr_px": mL, "text_w_px": text_w,
            "text_w_pct": round(pct, 2),
            "min_bottom_px": int(lim["min_bottom_px"]),
            "max_text_width_pct": lim["max_text_width_pct"]}


def write_ass(cues, cfg, out_path, lang=None, canvas=None):
    """canvas=(W,H)：字幕实际要烧上去的分辨率。

    为什么必须显式传：高清放大后画布从 1440x832 变成 2560x1480，
    PlayResX/Y 与实际分辨率不一致会让 libass 拉伸/偏移字幕（本项目踩过：
    「样式改了没生效」十有八九是 PlayRes 与视频分辨率不等）。
    字号/边距仍按百分比算 ⇒ 视觉比例与 1440p 时完全一致，只是像素值变大。
    """
    lang = lang or cfg["default_language"]
    lc = cfg["languages"][lang]
    lay = cfg["layout"]
    W, H = (canvas or (cfg["canvas"]["width"], cfg["canvas"]["height"]))
    _m = style_metrics(cfg, lang, (W, H))
    font_size, margin_v, margin_lr = _m["font_size"], _m["margin_v"], _m["margin_lr"]
    bold = -1 if lc.get("bold") else 0
    head = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "Title: %s" % os.path.basename(out_path),
        "PlayResX: %d" % W,
        "PlayResY: %d" % H,
        "WrapStyle: %d" % lay.get("WrapStyle", 2),
        "ScaledBorderAndShadow: %s" % lay.get("ScaledBorderAndShadow", "yes"),
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
         "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
         "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"),
        "Style: Default,%s,%d,%s,&H000000FF,%s,%s,%d,0,0,0,100,100,0,0,1,%d,%d,2,%d,%d,%d,%d" % (
            lc["font"], font_size, lay["primary_colour"], lay["outline_colour"],
            lay["back_colour"], bold, lc.get("outline", 3), lc.get("shadow", 1),
            margin_lr, margin_lr, margin_v, lay.get("encoding", 1)),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    body = []
    for c in cues:
        txt = "\\N".join(c["lines"]).replace("{", "(").replace("}", ")")
        body.append("Dialogue: 0,%s,%s,Default,,0,0,0,,%s" % (
            ass_time(c["start"]), ass_time(c["end"]), txt))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(head + body) + "\n")
    return {"font": lc["font"], "font_size_px": font_size,
            "margin_v_px": margin_v, "margin_lr_px": margin_lr,
            "font_size_pct": lc["font_size_pct"], "margin_v_pct": lc["margin_v_pct"],
            "font_size_by": _m["font_size_by"], "text_w_px": _m["usable_w"],
            "text_w_pct": round(_m["text_w_pct"], 2),
            "safe_area": {"min_bottom_px": int(_m["limits"]["min_bottom_px"]),
                          "max_text_width_pct": _m["limits"]["max_text_width_pct"]},
            "canvas": "%dx%d" % (W, H), "cues": len(body)}


# ---------------------------------------------------------------- 阶段 4/5：拼接与烧录

def stage_master(ws, ep, cfg, segs, subs_dir):
    """无损拼接（实测 0.35s / 集）。concat demuxer 的 list 必须 UTF-8 无 BOM 写。"""
    out = os.path.join(subs_dir, "%s_master.mp4" % ep)
    lst = os.path.join(subs_dir, "_concat.txt")
    lines = []
    for s in segs:
        lines.append("file '%s'" % s["file"].replace("\\", "/").replace("'", "'\\''"))
    with open(lst, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    run([cfg["tools"]["ffmpeg"], "-hide_banner", "-nostats", "-v", "error", "-y",
         "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])
    # ★ 2026-10-07：不删 concat 清单（safe-delete 钩子会话级计数，批量必炸），残留收尾统一清
    return out


def stage_render(cfg, master, ass, out, soft=False):
    r = cfg["render"]
    if soft:
        run([cfg["tools"]["ffmpeg"], "-hide_banner", "-nostats", "-v", "error", "-y",
             "-i", master, "-i", ass, "-c", "copy", "-c:s", "mov_text", out])
        return out
    vf = "subtitles=%s" % esc_filter_path(ass)
    run([cfg["tools"]["ffmpeg"], "-hide_banner", "-nostats", "-v", "error", "-y",
         "-i", master, "-vf", vf,
         "-c:v", r["video_codec"], "-crf", str(r["crf"]), "-preset", r["preset"],
         "-pix_fmt", r["pix_fmt"],
         "-c:a", r["audio_codec"], "-movflags", "+faststart", out])
    return out


def verify_render(cfg, master, subbed, cues, subs_dir, canvas=None):
    """机器断言字幕【真的画出来了】—— 本机无 vision provider，不能靠眼睛。

    原理：同一时间点分别抽「母版（无字幕）」与「成片（有字幕）」一帧，
    比较【字幕带】与【画面其余部分】的像素差异。
      * 字幕带差异应显著大于画面其余部分（证明变化是局部发生在字幕区）
      * 字幕带内的【近白像素】数量应明显增加（字幕是白字黑边）
    """
    try:
        import numpy as np
        from PIL import Image
    except Exception as e:
        return {"skipped": "缺少 numpy/Pillow: %s" % e}

    W, H = (canvas or (cfg["canvas"]["width"], cfg["canvas"]["height"]))
    lc = cfg["languages"][cfg["default_language"]]
    _sm = style_metrics(cfg, cfg["default_language"], (W, H))
    margin_v, fsz = _sm["margin_v"], _sm["font_size"]
    band_top = max(0, H - margin_v - int(fsz * 2.6))       # 两行字幕 + 边距
    cand = sorted([c for c in cues if c["end"] - c["start"] >= 1.0],
                  key=lambda c: c["end"] - c["start"], reverse=True)[:3] or cues[:1]

    results, best = [], None
    for c in cand:
        t = (c["start"] + c["end"]) / 2.0
        fa = os.path.join(subs_dir, "_vfy_master.png")
        fb = os.path.join(subs_dir, "_vfy_subbed.png")
        for src, dst in ((master, fa), (subbed, fb)):
            run([cfg["tools"]["ffmpeg"], "-hide_banner", "-nostats", "-v", "error", "-y",
                 "-ss", "%.3f" % t, "-i", src, "-frames:v", "1", dst])
        a = np.asarray(Image.open(fa).convert("RGB"), dtype=np.int16)
        b = np.asarray(Image.open(fb).convert("RGB"), dtype=np.int16)
        band = slice(band_top, H)
        other = slice(0, max(1, band_top - 60))
        d_band = float(np.abs(a[band] - b[band]).mean())
        d_other = float(np.abs(a[other] - b[other]).mean())
        w_a = int((a[band] > 225).all(axis=2).sum())
        w_b = int((b[band] > 225).all(axis=2).sum())
        r = {"t": round(t, 2), "text": c["text"][:20],
             "diff_band": round(d_band, 2), "diff_rest": round(d_other, 2),
             "white_master": w_a, "white_subbed": w_b, "white_gain": w_b - w_a}
        results.append(r)
        if best is None or r["white_gain"] > best["white_gain"]:
            best = r
    for p in ("_vfy_master.png", "_vfy_subbed.png"):
        # ★ 2026-10-07：不删验证截图（safe-delete 钩子会话级计数），残留收尾统一清
        pass

    verdict = {
        "band_top_px": band_top,
        "samples": results,
        "best": best,
        "pass": bool(best and best["white_gain"] > 200 and best["diff_band"] > best["diff_rest"]),
    }
    return verdict


# ---------------------------------------------------------------- 主流程

def main():
    harden_stdio()
    ap = argparse.ArgumentParser(description="可复用字幕管线（对齐 → ASS → 母版 → 烧录）")
    ap.add_argument("--ep", required=True, help="集号，如 ep03")
    ap.add_argument("--workspace", default=ROOT,
                    help="项目根目录；也可用环境变量 DRAMA_WORKSPACE 指定")
    ap.add_argument("--stage", default="all",
                    choices=["align", "cues", "ass", "master", "upscale", "render", "all"])
    ap.add_argument("--preview", type=int, default=0, metavar="N",
                    help="只用前 N 段做快速预览")
    ap.add_argument("--soft", action="store_true", help="软字幕（不重编视频）")
    ap.add_argument("--force-align", action="store_true")
    ap.add_argument("--force-upscale", action="store_true", help="忽略缓存重跑高清放大")
    ap.add_argument("--burn", action="store_true",
                    help="放完直接烧字幕（默认跑到 2K 母版就停下，等用户决定要不要字幕）")
    ap.add_argument("--no-upscale", action="store_true",
                    help="关掉高清放大：仍是旧行为（1440p 母版直接烧字幕）")
    ap.add_argument("--lang", default=None)
    args = ap.parse_args()

    if not args.workspace:
        raise SystemExit(
            "找不到短剧工作区。请二选一：\n"
            "  ① --workspace \"<项目根目录>\"\n"
            "  ② 设环境变量：$env:DRAMA_WORKSPACE = \"<项目根目录>\"  （PowerShell）\n"
            "工作区的标志物是 <项目根>/video-pipeline/data/ 与 config.subtitle.json。")
    ws = os.path.abspath(args.workspace)
    cfg = load_config(ws)
    if args.lang:
        cfg["default_language"] = args.lang
    subs_dir = os.path.join(ws, "video-pipeline", "output", "subs", args.ep)
    os.makedirs(subs_dir, exist_ok=True)

    # ---- 高清放大：决定「字幕最终烧在多大的画布上」------------------------
    up_mod, up_cfg, up_err = load_upscale(ws)
    up_on = (not args.no_upscale) and bool(up_mod) and bool(up_cfg) \
        and bool(up_cfg.get("enabled", True))
    if up_on and up_err:
        print("  ⚠ 高清放大不可用，按 1440p 处理：%s" % up_err)
        up_on = False

    t_start = time.time()
    segs, missing = episode_segments(ws, args.ep)
    if args.preview:
        segs = segs[:args.preview]
    if not segs:
        raise SystemExit("没有找到 %s 的成片段落" % args.ep)
    lines = dialogue_lines(ws, args.ep)

    src_w, src_h = video_dims(cfg["tools"]["ffprobe"], segs[0]["file"])
    if up_on:
        cw, ch, cf = up_mod.target_size({"width": src_w, "height": src_h}, up_cfg["target"])
    else:
        cw, ch, cf = cfg["canvas"]["width"], cfg["canvas"]["height"], 1.0
    canvas = (cw, ch)

    report = {"episode": args.ep, "workspace": ws, "segments": len(segs),
              "missing_segments": missing,
              "segments_used": [s["shot_id"] for s in segs],
              "config": {"language": cfg["default_language"]},
              "upscale": {"enabled": up_on, "target": "%dx%d" % canvas,
                          "factor": round(cf, 4),
                          "model": (up_cfg or {}).get("model", {}).get("name")}}

    print("集号 %s ｜ 段数 %d ｜ 缺失 %d" % (args.ep, len(segs), len(missing)))
    if missing:
        print("  ⚠ 缺失段：%s" % ", ".join(missing))
    print("分辨率路线：源 %dx%d → 字幕画布 %dx%d（%s）"
          % (src_w, src_h, cw, ch,
             "高清放大 %s 后烧字幕" % ((up_cfg or {}).get("model", {}).get("name"))
             if up_on else "未开高清放大"))

    if args.stage in ("align", "all"):
        n, sk = stage_align(ws, args.ep, cfg, segs, lines, subs_dir, force=args.force_align)
        print("① 顺序约束逐条对齐：新跑 %d 段 ／ 命中缓存 %d 段" % (n, sk))
        report["aligned_segments"] = n + sk
        report["align_cached"] = sk

    if args.stage in ("cues", "all", "ass", "render"):
        cues, warn, total, cps = build_cues(ws, args.ep, cfg, segs, lines, subs_dir)

        # ★ 短字幕修整（2026-10-07）：时长 < 0.3s 的条目（真实发音极短，如
        #   ep81「誰？」0.08s）优先向后延长，撞到下一条起点再向前延伸，
        #   保证 end-start >= 0.3 且不与相邻条重叠——0.08s 的字幕人眼根本看不到。
        cues.sort(key=lambda c: (c["start"], c["end"]))
        for _i, _c in enumerate(cues):
            if _c["end"] - _c["start"] >= 0.3:
                continue
            _nxt = cues[_i + 1]["start"] if _i + 1 < len(cues) else _c["end"] + 1.0
            _prv = cues[_i - 1]["end"] if _i > 0 else 0.0
            _c["end"] = min(max(_c["end"], _c["start"] + 0.3),
                            max(_nxt, _c["start"] + 0.3))
            if _c["end"] - _c["start"] < 0.3:
                _c["start"] = max(_prv, round(_c["end"] - 0.3, 3))

        dump_json(os.path.join(subs_dir, "cues.json"), cues)
        print("② 字幕条目：%d 条 ｜ 覆盖 %.1fs" % (len(cues), total))
        for w in warn:
            print("  ⚠ %s" % w)
        report["cues"] = len(cues)
        report["timeline_seconds"] = round(total, 3)
        report["warnings"] = warn
        report["cps_check"] = cps
        # ★ 语速断言：字/秒超过硬上限 = 对齐错位的铁证（不是"语速快"）
        if cps["cps_hard"]:
            _lc = cfg["languages"][cfg["default_language"]]
            raise SystemExit(
                "断言失败：%d 条字幕语速超过生理上限（>%.1f 字/秒），对齐必定错位。例：%s\n"
                "  排查：① 该段音频是否真的说了这句（用 whisper 独立转写核对）\n"
                "        ② 顺序约束是否失效（前一句终点被推太后，把后一句窗口压没）"
                % (len(cps["cps_hard"]), _lc.get("cps_impossible", 20.0),
                   cps["cps_hard"][0]))
        # 断言：时间必须合法（硬底线 0.3s；低于目标时长只告警，因为下一句紧跟时无法延长。
        # 0.3 口径：CTC 对齐出的真实短应答「好。」实测 0.32s（2026-10-07 ep01），
        # 0.4 会误杀——对齐成功的真实语速不该被断言判死；0.3 以下才真的看不清）
        HARD_MIN = 0.3
        bad = [c for c in cues if c["start"] < 0 or c["end"] > total + 0.5
               or c["end"] - c["start"] < HARD_MIN]
        if bad:
            raise SystemExit("断言失败：%d 条字幕时间非法，例：%s" % (len(bad), bad[0]))

    ass = os.path.join(subs_dir, "%s.ass" % args.ep)
    if args.stage in ("ass", "all", "render"):
        info = write_ass(cues, cfg, ass, canvas=canvas)
        print("③ ASS：%s" % ass)
        print("   字体=%s 字号=%dpx(%.1f%%) 底部间距=%dpx(%.1f%%) 左右=%dpx 画布=%s 条目=%d"
              % (info["font"], info["font_size_px"], info["font_size_pct"],
                 info["margin_v_px"], info["margin_v_pct"], info["margin_lr_px"],
                 info["canvas"], info["cues"]))
        print("   字号依据=%s ｜ 文本框宽=%dpx(%.1f%%) ｜ 安全区：底≥%dpx 宽≤%.0f%%"
              % (info["font_size_by"], info["text_w_px"], info["text_w_pct"],
                 info["safe_area"]["min_bottom_px"], info["safe_area"]["max_text_width_pct"]))
        report["ass"] = info
        qa = qa_safe_area(ass, cfg)
        print("   ★ 安全区断言：%s" % ("✅ 通过" if qa["pass"] else "❌ " + "；".join(qa["failures"])))
        report["ass_safe_area"] = qa
        if not qa["pass"]:
            raise SystemExit("断言失败（字幕安全区）：%s" % "；".join(qa["failures"]))

    master = None
    if args.stage in ("master", "all", "render", "upscale"):
        master = stage_master(ws, args.ep, cfg, segs, subs_dir)
        fr = ffprobe_frames(cfg["tools"]["ffprobe"], master)
        print("④ 母版（无损拼接）：%s ｜ %d 帧" % (master, fr))
        report["master"] = {"path": master, "frames": fr}

    # ---- ⑤ 高清放大（合并之后、烧字幕之前）--------------------------------
    two_k = os.path.join(subs_dir, "%s_master_2k.mp4" % args.ep)
    up_result = None
    want_up = up_on and args.stage in ("upscale", "all", "render")
    if want_up:
        if master is None and not os.path.exists(two_k):
            raise SystemExit("要先有母版才能放大：%s 不存在（先跑 --stage master）" % two_k)
        print("⑤ 高清放大（Topaz %s）：%dx%d → %dx%d …"
              % (up_cfg["model"]["name"], src_w, src_h, cw, ch))
        up_result = up_mod.upscale_episode(ws, args.ep, up_cfg,
                                           force=args.force_upscale, quiet=False)
        report["upscale_result"] = up_result
        if not up_result.get("ok"):
            raise SystemExit(
                "高清放大失败：%s\n%s"
                % (up_result.get("error") or "自检未通过",
                   up_result.get("hint") or
                   "  排查：① Topaz 模型权重是否在本机（脚本会打印探测到的目录）\n"
                   "        ② 想先跳过放大：--no-upscale；只想要分辨率数字变大：--fallback-lanczos"))
        v = up_result.get("verify") or {}
        if up_result.get("cached"):
            print("   命中缓存，跳过重跑")
        else:
            print("   完成：%.1fs ｜ %.2f 帧/秒 ｜ %s"
                  % (up_result.get("elapsed_s") or 0, up_result.get("avg_fps") or 0,
                     two_k))
        for c in v.get("checks", []):
            print("   %s %s —— %s" % ("[OK]" if c["ok"] else "[FAIL]", c["name"], c["detail"]))
        report["upscale_path"] = two_k

    # 字幕烧在哪个视频上：有 2K 就用 2K
    base_video = master
    if up_on and os.path.exists(two_k) and args.stage in ("upscale", "all", "render"):
        base_video = two_k

    # --stage all 的收尾语义：
    #   * 开了放大 → 跑到 2K 母版就【停】，等用户回答要不要字幕（这是用户明确要求的停点）
    #   * --burn   → 不停，直接烧
    #   * --no-upscale → 回到旧行为（一条链跑到底，1440p 直接烧），别让老脚本突然"什么都没出"
    do_render = args.stage == "render" or (args.stage == "all" and (args.burn or not up_on))
    if do_render:
        if base_video is None:
            raise SystemExit("没有可烧字幕的母版（先跑 --stage master 或 --stage upscale）")
        # ★ 画布必须等于【真的要烧的那条视频】的分辨率，否则 libass 会拉伸/偏移字幕
        real = video_dims(cfg["tools"]["ffprobe"], base_video)
        if real != canvas:
            print("   ⚠ 母版实际 %dx%d ≠ 预期 %dx%d，按实际分辨率重写 ASS"
                  % (real[0], real[1], canvas[0], canvas[1]))
            canvas = real
            info2 = write_ass(cues, cfg, ass, canvas=canvas)
            report["canvas_corrected"] = "%dx%d" % canvas
            qa2 = qa_safe_area(ass, cfg)
            print("   重写后几何：字号=%dpx(%s) 距底=%dpx 文本框=%dpx(%.1f%%) ★ 安全区断言：%s"
                  % (info2["font_size_px"], info2["font_size_by"], info2["margin_v_px"],
                     info2["text_w_px"], info2["text_w_pct"],
                     "✅ 通过" if qa2["pass"] else "❌ " + "；".join(qa2["failures"])))
            report["ass_safe_area"] = qa2
            if not qa2["pass"]:
                raise SystemExit("断言失败（字幕安全区，按实际分辨率重写后）：%s"
                                 % "；".join(qa2["failures"]))
        out = os.path.join(subs_dir, "%s_%s.mp4" % (args.ep, "softsub" if args.soft else "subbed"))
        # ★ 不许静默覆盖更好的一版：已有成片的分辨率比这次要高时，先改名留存再渲染
        #   （踩点：先跑了 2K，后来又用 --no-upscale 跑一次 1440p，2K 成片就被盖掉了）
        if os.path.exists(out):
            try:
                old = video_dims(cfg["tools"]["ffprobe"], out)
            except Exception:                 # ★ SystemExit 之外还有 RuntimeError：
                old = canvas                  #   被杀进程留下的损坏 mp4（moov 缺失）
                print("   ⚠ 旧成片读取失败（疑似损坏）→ 视为同分辨率，直接重烧覆盖")
            if old[0] * old[1] > canvas[0] * canvas[1]:
                kept = os.path.join(subs_dir, "%s_%s_%dx%d.mp4"
                                    % (args.ep, "softsub" if args.soft else "subbed",
                                       old[0], old[1]))
                if not os.path.exists(kept):
                    os.replace(out, kept)
                    print("   ⚠ 原成片分辨率更高（%dx%d），已改名留存：%s"
                          % (old[0], old[1], os.path.basename(kept)))
                else:
                    print("   ⚠ 原有更高分辨率成片已存在于 %s，本次直接覆盖同名文件"
                          % os.path.basename(kept))
        stage_render(cfg, base_video, ass, out, soft=args.soft)
        fr = ffprobe_frames(cfg["tools"]["ffprobe"], out)
        exp = ffprobe_frames(cfg["tools"]["ffprobe"], base_video)
        ok = (fr == exp) if exp else None
        print("⑥ 成片：%s ｜ %d 帧 ｜ 与母版一致：%s" % (out, fr, ok))
        if exp and not ok:
            raise SystemExit("断言失败：成片帧数 %d != 母版 %d" % (fr, exp))
        report["output"] = {"path": out, "frames": fr, "frames_match": ok,
                            "base": base_video, "canvas": "%dx%d" % canvas}

        if not args.soft:
            v = verify_render(cfg, base_video, out, cues, subs_dir, canvas=canvas)
            report["verify_render"] = v
            if "skipped" in v:
                print("⑦ 渲染自检：跳过（%s）" % v["skipped"])
            else:
                b = v["best"]
                print("⑦ 渲染自检（字幕带 y≥%d）：" % v["band_top_px"])
                print("   t=%.2fs 「%s」  字幕带差异=%.1f  画面其余差异=%.1f  近白像素 %d→%d (增 %+d)"
                      % (b["t"], b["text"], b["diff_band"], b["diff_rest"],
                         b["white_master"], b["white_subbed"], b["white_gain"]))
                print("   ⇒ %s" % ("✅ 字幕确实画出来了" if v["pass"]
                                   else "❌ 未检出字幕（渲染可能失败）"))
                if not v["pass"]:
                    raise SystemExit("断言失败：渲染自检未通过")
        report["elapsed_s"] = round(time.time() - t_start, 1)
        print("\n✅ 完成，用时 %.1fs" % report["elapsed_s"])
        print("   看片：%s" % out)

    # ---- 停在「2K 母版已就绪」，把决定权交回用户 ---------------------------
    if args.stage == "all" and up_on and not args.burn and os.path.exists(two_k):
        print("")
        print("=" * 74)
        print(" 2K 母版已就绪（无字幕）：%s" % two_k)
        print("")
        print(" 要不要加字幕？（这一步要你确认，因为烧字幕要重编码一遍，约 1–3 分钟）")
        print("   要 → python scripts/subtitle.py --workspace \"%s\" --ep %s --stage render"
              % (ws, args.ep))
        print("   不要 → 直接交付上面那个 mp4")
        print("")
        print(" 为什么先放大后烧字幕：烧字幕必然重编码，若先烧再放大，")
        print(" 字幕会被 AI 超分当成画面纹理一起「重绘」（描边发糊、笔画变形）。")
        print("=" * 74)

    dump_json(os.path.join(subs_dir, "report.json"), report)
    print("   报告：%s" % os.path.join(subs_dir, "report.json"))


if __name__ == "__main__":
    main()
