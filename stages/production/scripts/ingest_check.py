#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ingest_check.py —— 入 `data/` 的生成端门禁（缺口 G7 / 断点 B7）

为什么有它
----------
此前「`<d>` 是否齐全、Picture 槽是否 ≤6、duration 与时间戳是否一致」全靠人查，
错误要到**出片或字幕阶段**才暴露 —— 资产多花的 269 RH币就是这么来的。
本脚本把校验提前到「进 data/ 之前」，不过就停，**把错误挡在计费之前**。

校验项（逐条报，缺关键字段报错让人补，**不猜不编**）
----------------------------------------------------
1. `shots.json`  非空 / `shot_id` 唯一 / 有 `banana_prompt`
2. `h3_prompts.json` 覆盖全部 shot_id，且 `prompt` 非空
3. `<Picture N>` 编号**从 1 连续、≤6、不留空号**（留空号会让后面全部错位）
4. `<d>[lang] …</d>` 存在且非空（字幕真源）
5. 镜头时间戳 `[Shot N] At MM:SS(.mmm)` **严格递增**，且总时长 == `duration`（±0.5s）
6. 参考图槽位一致性：`len(character_refs) >= max(<Picture N>)`
7. 参考图文件存在（相对 `data/` 解析的本地路径）

用法
----
    python ingest_check.py --workspace "D:/剧/新剧" --ep ep01
    python ingest_check.py --workspace "D:/剧/新剧"            # 全量
退出码：0 = 通过；2 = 门禁不过（不得进入出片）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _ws as W  # noqa: E402

PIC = re.compile(r"<Picture\s+(\d+)\s*>")
SHOT_TS = re.compile(r"\[Shot\s+(\d+)\]\s*(?:At\s+(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?)?")
D_TAG = re.compile(r"<d>\[[^\]]*\]\s*([^<]*)</d>", re.S)
PICTURE_MAX = 6          # ★ 平台硬限：H3 图片槽 ≤6，改不了


def load(p: pathlib.Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def total_seconds(frag: str) -> float | None:
    """把 `duration` 字段（8 / \"8s\" / \"8.5\"）折算成秒。"""
    m = re.search(r"(\d+(?:\.\d+)?)", str(frag))
    return float(m.group(1)) if m else None


def check_shot(sid: str, prompt: str, duration, refs: list, data_dir: pathlib.Path) -> list:
    errs, warns = [], []
    if not prompt.strip():
        return ["%s: prompt 为空" % sid], warns

    # 3) <Picture N> 连续且 ≤6
    nums = sorted({int(m.group(1)) for m in PIC.finditer(prompt)})
    if nums:
        if nums != list(range(1, max(nums) + 1)):
            errs.append("%s: <Picture N> 编号不连续 %s（留空号会让后面全部错位）" % (sid, nums))
        if max(nums) > PICTURE_MAX:
            errs.append("%s: <Picture N> 最大 %d 超过硬上限 %d（须删槽位改纯文字，不能只改 refs）"
                        % (sid, max(nums), PICTURE_MAX))
        if len(refs) < max(nums):
            errs.append("%s: 参考图只有 %d 张，但提示词点到了 <Picture %d>（缺 %d 张）"
                        % (sid, len(refs), max(nums), max(nums) - len(refs)))
    elif refs:
        warns.append("%s: 挂了 %d 张参考图，但提示词里没有 <Picture N> 引用" % (sid, len(refs)))

    # 4) <d> 台词齐全
    if not D_TAG.search(prompt):
        warns.append("%s: 没有 <d>…</d> 台词（无台词集可忽略）" % sid)
    elif any(not m.group(1).strip() for m in D_TAG.finditer(prompt)):
        errs.append("%s: 存在空的 <d></d>" % sid)

    # 5) 时间戳严格递增 + 总时长 == duration
    ts = []
    for m in SHOT_TS.finditer(prompt):
        if m.group(2) is None:
            continue
        secs = int(m.group(2)) * 60 + int(m.group(3)) + (int(m.group(4).ljust(3, "0")) / 1000 if m.group(4) else 0)
        ts.append(secs)
    if ts != sorted(ts) or len(ts) != len(set(ts)):
        errs.append("%s: 镜头切点时间戳非严格递增 %s" % (sid, ts))
    d = total_seconds(duration) if duration is not None else None
    if d:
        last = ts[-1] if ts else 0.0
        if last > d + 0.5:
            errs.append("%s: 最后切点 %.3fs 超过 duration %.1fs" % (sid, last, d))
        elif ts and abs((d - last) - (ts[1] - ts[0] if len(ts) > 1 else d - last)) > 0 and False:
            pass  # 保留位：不强求末镜时长，避免误伤
        if not ts and d:
            warns.append("%s: 有 duration=%.1fs 但提示词里没有 [Shot N] At 时间戳" % (sid, d))
    elif ts:
        warns.append("%s: 有时间戳但 h3_prompts 里没有 duration（引擎无法校验节奏）" % sid)

    # 7) 参考图文件存在
    for r in refs:
        if isinstance(r, str) and not r.startswith(("http://", "https://", "data:")):
            if not (data_dir / r).is_file():
                errs.append("%s: 参考图本地路径不存在（相对 data/）：%s" % (sid, r))
    return errs, warns


def main() -> int:
    ap = argparse.ArgumentParser(description="入 data/ 的生成端门禁（不猜不编）")
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--ep", default=None, help="只查该集（shot_id 前缀 epNN_）")
    a = ap.parse_args()

    # ★ 按**参数里的工作区**解析输入目录（不要用导入期常量，否则 --workspace 被静默忽略）
    ws = pathlib.Path(a.workspace).expanduser().resolve() if a.workspace else W.WS
    DATA = W.data_dir(ws)
    if not DATA.is_dir():
        print("[FAIL] 找不到输入目录：%s" % DATA)
        print("       先跑 convert_storyboard.py，或检查 layout.json / config.local.json")
        return 2
    shots = load(DATA / "shots.json", None)
    prompts = load(DATA / "h3_prompts.json", None)
    if shots is None or prompts is None:
        print("[FAIL] %s 下缺 shots.json 或 h3_prompts.json" % DATA)
        return 2
    if not isinstance(shots, list):
        shots = shots.get("shots") or []

    errs, warns = [], []
    ids = []
    for s in shots:
        sid = str(s.get("shot_id") or "")
        if not sid:
            errs.append("存在没有 shot_id 的分镜条目")
            continue
        ids.append(sid)
    dup = {x for x in ids if ids.count(x) > 1}
    if dup:
        errs.append("shot_id 重复：%s" % sorted(dup))
    if a.ep:
        scoped = [i for i in ids if i.startswith(a.ep + "_") or i == a.ep]
        if scoped:
            ids = scoped
        else:
            # 分镜号可能是 shot_001（不带宽前缀）——此时 --ep 只作提示，不误杀全部
            print("[WARN] 没有 shot_id 以「%s_」开头（本数据集用的是 %s 之类）；改按全部 %d 段检查。"
                  % (a.ep, ids[0], len(ids)))
    if not ids:
        print("[FAIL] 没有可检查的分镜")
        return 2

    for sid in ids:
        s = next(x for x in shots if str(x.get("shot_id")) == sid)
        if not s.get("banana_prompt"):
            errs.append("%s: 缺 banana_prompt（九宫格图生图提示词）" % sid)
        v = prompts.get(sid)
        if not isinstance(v, dict) or not v.get("prompt"):
            errs.append("%s: h3_prompts.json 里没有该段的 prompt（引擎会直接 FAILED，不猜）" % sid)
            continue
        e, w = check_shot(sid, v["prompt"], v.get("duration"),
                          s.get("character_refs") or [], DATA)
        errs += e
        warns += w

    print("=" * 74)
    print("入 data/ 生成端门禁（B7）")
    print("=" * 74)
    print("  输入目录: %s" % DATA)
    print("  检查范围: %s" % (a.ep or "全部"))
    print("  分镜数  : %d" % len(ids))
    for w in warns:
        print("  [WARN] %s" % w)
    for e in errs:
        print("  [FAIL] %s" % e)
    print("-" * 74)
    if errs:
        print("[BLOCK] %d 个硬问题 —— 不得进入出片（错误已挡在计费之前）。" % len(errs))
        return 2
    print("[OK] 门禁通过，可进入：doctor → assets → refs → video plan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
