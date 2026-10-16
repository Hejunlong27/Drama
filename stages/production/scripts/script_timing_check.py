#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""script_timing_check.py —— 台词时长审计（4.2 字/秒折算，30 秒文戏单元口径）

为什么有它（缺口 G3，2026-10-16 补）
------------------------------------
`script-timing-resegment` 技能只能靠人喊才跑；本脚本把它固化成**项目内可预置**的确定性检查，
在「分镜/提示词产出后 → 进入出片前」执行一次，避免成片节奏错位。

时间模型（口径与 `script-timing-resegment` 一致，**不得私改**）
----------------------------------------------------------------
* **有效台词**：镜内引号中全部人声（对白、画外音、OS、机械播报音），不含标点与说明文字。
* **折算语速**：**4.2 字/秒**（短剧配音中速，含正常停顿）。
* **单元容量**：30 秒单元目标 60-100 字；>100 字 = 超载；<45 字 = 偏轻（动作/空镜可接受）。
* **换算表**：30s→上限 100 / 目标 60-90；15s→上限 55 / 目标 25-50；10s→上限 35 / 目标 18-32。
  通用式：上限 = round(4.2 × 秒 × 0.79)，目标下限 = round(上限 × 0.55)。

输入（二选一）
--------------
    --json  <h3_prompts.json>   按 shot 取 `prompt` 里的 `<d>[lang] …</d>` 与 `duration`
    --md    <分镜稿.md>          按 `## 第N段` / `## shot_xxx` 切段，抽引号内台词

输出
----
* 控制台审计表 + 汇总；
* `--report <path>` 落 Markdown 报告（默认写到 `<06-提示词>/台词时长审计.md`）
* 退出码：0 = 无超载；2 = 存在超载段（应由上层拦下，**不要靠加速字幕硬塞**）

用法
----
    python script_timing_check.py --workspace "D:/剧/新剧" --ep ep01
    python script_timing_check.py --md "D:/剧/新剧/05-分镜/ep01.md" --unit 30
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _ws as W  # noqa: E402  ★ 分阶段目录真源（见 stages/_layout.py）

CPS = 4.2                      # ★ 折算语速：字/秒（硬口径）
OVERLOAD_RATIO = 0.79          # 上限系数：上限 = round(CPS × 秒 × 0.79)
LIGHT_RATIO = 0.55             # 目标下限系数
LIGHT_HARD = 45                # 绝对偏轻线（30s 单元口径）

# 单元时长 → (上限字数, 目标下限, 目标上限)，与技能口径逐字对齐
UNIT_TABLE = {
    30: (100, 60, 90),
    15: (55, 25, 50),
    10: (35, 18, 32),
}

D_LINE = re.compile(r"<d>\[([^\]]*)\]\s*([^<]*)</d>", re.S)
QUOTED = re.compile(r"[“\"'「]([^”\"'」]+)[”\"'」]")
SEG_HEAD = re.compile(r"(?m)^##+\s*(?:第\s*(\d+)\s*段|(shot[_A-Za-z0-9]+)|(\S+))")
CJK_OR_WORD = re.compile(r"[\u4e00-\u9fff]|[A-Za-z]+")


def count_line(text: str) -> int:
    """有效台词字数：汉字按 1 字；英文按 1 词 1 字；标点/空白不计。"""
    return len(CJK_OR_WORD.findall(text or ""))


def limit_for(seconds: float) -> tuple:
    """返回 (上限, 目标下限, 目标上限)。表内取表，表外按系数算。"""
    if int(seconds) in UNIT_TABLE:
        return UNIT_TABLE[int(seconds)]
    up = max(1, round(CPS * seconds * OVERLOAD_RATIO))
    return up, max(1, round(up * LIGHT_RATIO)), up


def audit_rows(rows: list) -> list:
    """rows: [{'id': str, 'seconds': float, 'chars': int, 'text': str}] → 判定结果。"""
    out = []
    for r in rows:
        up, lo, hi = limit_for(r["seconds"])
        c = r["chars"]
        if c > up:
            verdict = "超载"
        elif c < lo:
            verdict = "偏轻"
        else:
            verdict = "正常"
        out.append({**r, "limit": up, "target": "%d-%d" % (lo, hi),
                    "cps": round(c / r["seconds"], 2) if r["seconds"] else 0.0,
                    "verdict": verdict})
    return out


def rows_from_json(p: pathlib.Path) -> list:
    data = json.loads(p.read_text(encoding="utf-8"))
    rows = []
    for sid, v in (data or {}).items():
        if not isinstance(v, dict):
            continue
        prompt = v.get("prompt") or ""
        text = " ".join(m.group(2) for m in D_LINE.finditer(prompt))
        sec = float(v.get("duration") or 0) or float(v.get("seconds") or 0)
        rows.append({"id": sid, "seconds": sec, "chars": count_line(text), "text": text.strip()})
    return rows


def rows_from_md(p: pathlib.Path, unit: float | None) -> list:
    text = p.read_text(encoding="utf-8", errors="replace")
    heads = list(SEG_HEAD.finditer(text))
    rows = []
    if not heads:
        body = text
        rows.append({"id": p.stem, "seconds": unit or 30.0,
                     "chars": count_line(" ".join(QUOTED.findall(body))), "text": ""})
        return rows
    for i, h in enumerate(heads):
        seg = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        # 单位内只取 `镜头N：` 行里的引号，避免人物站位里的引号污染统计（技能踩过的坑）
        lines = [ln for ln in seg.splitlines() if re.match(r"^\s*镜头\s*\d+\s*[:：]", ln)]
        joined = "\n".join(lines) if lines else seg
        # 段落内显式写了时长就用它（`约12秒` / `12s` / `duration: 12`）
        m = (re.search(r"(?:约\s*)?(\d+(?:\.\d+)?)\s*(?:秒|s\b)", seg)
             or re.search(r"(?im)^\s*(?:duration|时长)\s*[:：]\s*(\d+(?:\.\d+)?)", seg))
        sec = float(m.group(1)) if m else (unit or 30.0)
        rows.append({"id": (h.group(2) or h.group(1) or h.group(3) or "seg"),
                     "seconds": sec, "chars": count_line(" ".join(QUOTED.findall(joined))),
                     "text": ""})
    return rows


def render(rows: list, src: str) -> tuple:
    lines = ["# 台词时长审计（4.2 字/秒）", "",
             "- 数据源：`%s`" % src,
             "- 折算语速：**%.1f 字/秒**；单元容量见 30s→100 字 / 15s→55 字 / 10s→35 字", ]

    head = "| 段 | 时长(秒) | 台词字数 | 字数/秒 | 容量上限 | 目标区间 | 判定 |"
    sep = "|---|---|---|---|---|---|---|"
    body = ["| %s | %s | %d | %.2f | %d | %s | %s |"
            % (r["id"], ("%g" % r["seconds"]), r["chars"], r["cps"], r["limit"], r["target"], r["verdict"])
            for r in rows]
    over = [r for r in rows if r["verdict"] == "超载"]
    light = [r for r in rows if r["verdict"] == "偏轻"]
    md = lines[:3] + ["", head, sep] + body + ["",
         "- 合计：%d 段 ｜ 超载 **%d** ｜ 偏轻 %d ｜ 正常 %d"
         % (len(rows), len(over), len(light), len(rows) - len(over) - len(light)), ""]
    if over:
        md += ["## 超载段（必须重切，禁止靠加速字幕硬塞）", ""]
        for r in over:
            need = -(-r["chars"] // r["limit"])          # ceil
            md.append("- `%s`：%d 字 / %s 秒（上限 %d）→ 至少切成 **%d** 段"
                      % (r["id"], r["chars"], ("%g" % r["seconds"]), r["limit"], need))
    md.append("")
    return "\n".join(md), over


def main() -> int:
    ap = argparse.ArgumentParser(description="台词时长审计（4.2 字/秒折算）")
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--ep", default=None, help="只审该集（按 shot_id 前缀 epNN_ 过滤）")
    ap.add_argument("--json", default=None, help="直接指定 h3_prompts.json")
    ap.add_argument("--md", default=None, help="直接指定分镜稿 md")
    ap.add_argument("--unit", type=float, default=None, help="md 模式下默认单元秒数（默认 30）")
    ap.add_argument("--report", default=None, help="报告输出路径（默认 <DATA>/台词时长审计.md）")
    a = ap.parse_args()

    if a.md:
        p = pathlib.Path(a.md).expanduser().resolve()
        if not p.is_file():
            print("[FAIL] 找不到 md：%s" % p)
            return 2
        rows, src = rows_from_md(p, a.unit), str(p)
        default_report = None
    else:
        default_data = W.data_dir(a.workspace)
        p = pathlib.Path(a.json).expanduser().resolve() if a.json else (default_data / "h3_prompts.json")
        if not p.is_file():
            print("[FAIL] 找不到 h3_prompts.json：%s" % p)
            print("       出片引擎输入目录 = %s" % default_data)
            return 2
        rows, src = rows_from_json(p), str(p)
        default_report = None
        try:
            import _layout as _L
            default_report = _L.stage_dir(pathlib.Path(a.workspace).resolve()
                                           if a.workspace else W.WS, "script") / "台词时长审计.md"
        except Exception:                                                     # noqa: BLE001
            default_report = default_data.parent / "台词时长审计.md"

    if a.ep:
        scoped = [r for r in rows
                  if str(r["id"]).startswith(a.ep + "_") or str(r["id"]) == a.ep]
        if scoped:
            rows = scoped
        else:
            print("[WARN] 没有段号以「%s_」开头（本数据集用的是 %s 之类）；改按全部 %d 段审计。"
                  % (a.ep, rows[0]["id"], len(rows)))
    if not rows:
        print("[FAIL] 没有可审计的段（%s）" % src)
        return 2

    rows = audit_rows(rows)
    md, over = render(rows, src)
    print(md)

    out = pathlib.Path(a.report).expanduser().resolve() if a.report else default_report
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md + "\n", encoding="utf-8")
        print("[OK] 报告：%s" % out)

    if over:
        print("\n[BLOCK] 存在 %d 个超载段 —— 请在关系转折处重切，不要加速字幕或整体加速。" % len(over))
        return 2
    print("\n[OK] 无超载段。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
