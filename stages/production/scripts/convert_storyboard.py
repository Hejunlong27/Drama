#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""convert_storyboard.py —— 分镜/提示词 md → `data/` 四文件（缺口 G7 / 断点 B1）

为什么有它
----------
可行性报告把「创作层产物（md）→ 出片引擎输入（`data/` 四文件）」定为**唯一 P0 断点**：
上游产出的是给人看的 Markdown，下游吃的是严格 schema 的 JSON，中间此前靠**手工搬**。
本脚本把这一步固化成可复现的转换，**只做搬运与校验，不改一个字提示词**。

输入契约（md，容错解析）
------------------------
    ## shot_001
    - duration: 8
    - ratio: 9:16
    - refs: char_01, char_02, scene_01
    - banana_prompt: <九宫格图生图提示词，单行>
    - prompt: |
        <H3 六段式提示词全文，缩进块>
        ...

兼容写法：`prompt:` 后接 ``` 围栏代码块，或直接缩进 4 空格；键名大小写与中划线/下划线均可。
`## 第N段` 亦可（段号自动转为 shot_id）。

输出（`data/` 四文件，schema 见 stages/video/workflow/references/data-schema.md）
--------------------------------------------------------------------------------
    shots.json       [{shot_id, banana_prompt, ratio, crop, character_refs, scene_refs}]
    h3_prompts.json  {shot_id: {prompt, duration, ratio}}
    characters.json  {key: 图片路径}   ← 自 `<04-资产>/refs.json` 合并（可选）
    scenes.json      {key: 图片路径}   ← 同上

用法
----
    python convert_storyboard.py --workspace "D:/剧/新剧" --ep ep01            # 干跑对比
    python convert_storyboard.py --workspace "D:/剧/新剧" --ep ep01 --apply    # 真写（先备份）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _ws as W  # noqa: E402  ★ 分阶段目录真源（stages/_layout.py）
import _layout as L  # noqa: E402

HEAD = re.compile(r"(?m)^##+\s*(.+?)\s*$")
KV = re.compile(r"(?m)^\s*[-*]?\s*([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff\-]*)\s*[:：]\s*(.*)$")
FENCE = re.compile(r"```[A-Za-z]*\s*\n(.*?)```", re.S)
SHOT_ALIASES = {"duration": "duration", "时长": "duration", "秒": "duration",
                "ratio": "ratio", "画幅": "ratio",
                "refs": "refs", "参考图": "refs", "picture": "refs",
                "banana_prompt": "banana_prompt", "banana": "banana_prompt",
                "九宫格": "banana_prompt",
                "prompt": "prompt", "h3": "prompt", "提示词": "prompt",
                "crop": "crop"}


def _norm_key(k: str) -> str:
    k = k.strip().lower().replace("-", "_")
    return SHOT_ALIASES.get(k, SHOT_ALIASES.get(k.replace("_", ""), k))


def _shot_id(raw: str) -> str:
    raw = raw.strip()
    m = re.fullmatch(r"第\s*(\d+)\s*段", raw)
    if m:
        return "shot_%03d" % int(m.group(1))
    m = re.fullmatch(r"(?:EP)?\s*(\d+)", raw)
    if m:
        return "shot_%03d" % int(m.group(1))
    return re.sub(r"[^\w\-]", "_", raw)


def parse_md(text: str) -> list:
    """把 md 解析成 shot 列表。返回 [{'shot_id', 'fields': {...}, 'prompt': str}]"""
    heads = list(HEAD.finditer(text))
    out = []
    for i, h in enumerate(heads):
        body = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        sid = _shot_id(h.group(1))
        if sid in ("", "_"):
            continue
        fields, prompt = {}, ""
        # 1) 优先取围栏代码块作为 prompt（避免与字段行混淆）
        fm = FENCE.search(body)
        if fm:
            prompt = fm.group(1).strip()
            body = body[:fm.start()] + body[fm.end():]
        # 2) 字段行
        for m in KV.finditer(body):
            k, v = _norm_key(m.group(1)), m.group(2).strip()
            if not v:
                continue
            if k == "prompt" and not prompt:
                # `prompt: |` 形式 —— 取其后缩进块
                tail = body[m.end():]
                block = "\n".join(ln for ln in tail.splitlines()[:40]
                                  if ln.startswith(("    ", "\t")))
                prompt = block.strip() or v
            else:
                fields.setdefault(k, v)
        out.append({"shot_id": sid, "fields": fields, "prompt": prompt})
    return out


def _crop(v: str | None):
    if not v:
        return {"rows": 3, "cols": 3}
    m = re.fullmatch(r"(\d+)\s*[x×]\s*(\d+)", v.strip())
    return {"rows": int(m.group(1)), "cols": int(m.group(2))} if m else {"rows": 3, "cols": 3}


def build(shots: list, ws: pathlib.Path) -> tuple:
    """→ (data 四文件 dict, 问题列表)"""
    problems, out_shots, out_h3 = [], [], {}
    seen = set()
    for s in shots:
        sid, f = s["shot_id"], s["fields"]
        if sid in seen:
            problems.append("shot_id 重复：%s" % sid)
            continue
        seen.add(sid)
        if not s["prompt"].strip():
            problems.append("%s 缺 prompt（H3 六段式）" % sid)
        if not f.get("banana_prompt"):
            problems.append("%s 缺 banana_prompt（九宫格图生图提示词）" % sid)
        refs = [x.strip() for x in re.split(r"[,，、\s]+", f.get("refs") or "") if x.strip()]
        out_shots.append({
            "shot_id": sid,
            "banana_prompt": f.get("banana_prompt") or "",
            "ratio": f.get("ratio") or "9:16",
            "crop": _crop(f.get("crop")),
            # ★ 约定：`refs` 顺序 = 提示词 `<Picture N>` 顺序（引擎按顺序喂槽，缺一号后面全错位）
            "character_refs": refs,
        })
        dur = f.get("duration")
        out_h3[sid] = {"prompt": s["prompt"].strip(),
                       **({"duration": float(re.sub(r"[^\d.]", "", dur))} if dur else {}),
                       "ratio": f.get("ratio") or "9:16"}

    # characters/scenes：从 04-资产/refs.json 合并（存在才合）
    refs = {}
    try:
        rp = L.stage_dir(ws, "assets") / "refs.json"
        if rp.is_file():
            refs = json.loads(rp.read_text(encoding="utf-8-sig")) or {}
    except Exception:                                                          # noqa: BLE001
        refs = {}
    chars = refs.get("characters") or {}
    scenes = refs.get("scenes") or {}
    return {"shots.json": out_shots, "h3_prompts.json": out_h3,
            "characters.json": chars, "scenes.json": scenes}, problems


def main() -> int:
    ap = argparse.ArgumentParser(description="分镜/提示词 md → data/ 四文件（只搬运不改写）")
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--ep", required=True, help="集号，如 ep01")
    ap.add_argument("--src", default=None, help="md 路径；默认自动在 05-分镜 / 06-提示词 里找")
    ap.add_argument("--apply", action="store_true", help="真写 data/（先备份原文件）")
    a = ap.parse_args()

    ws = pathlib.Path(a.workspace).expanduser().resolve() if a.workspace else W.WS
    src = pathlib.Path(a.src).expanduser().resolve() if a.src else None
    if src is None:
        cands = []
        for key in ("storyboard", "prompts"):
            try:
                d = L.stage_dir(ws, key)
            except Exception:                                                  # noqa: BLE001
                continue
            cands += sorted(d.glob("%s*.md" % a.ep))
        src = next((c for c in cands if c.is_file()), None)
    if not src or not src.is_file():
        print("[FAIL] 找不到 %s 的分镜/提示词 md（用 --src 指定）" % a.ep)
        return 2

    data_dir = W.data_dir(ws)
    shots = parse_md(src.read_text(encoding="utf-8", errors="replace"))
    if not shots:
        print("[FAIL] %s 里没有解析到任何 `## <shot_id>` 段落" % src)
        return 2
    files, problems = build(shots, ws)

    print("=" * 74)
    print("分镜/提示词 md → data/ 四文件")
    print("=" * 74)
    print("  源文件  : %s" % src)
    print("  输入目录: %s" % data_dir)
    print("  解析到  : %d 段" % len(shots))
    for name, obj in files.items():
        print("    %-18s %d 条" % (name, len(obj)))
    if problems:
        print("\n  [WARN] %d 个问题（不猜不编，请回上游补齐）：" % len(problems))
        for p in problems[:20]:
            print("        - %s" % p)

    if not a.apply:
        print("\n（干跑：未写盘。加 --apply 真写。）")
        return 0

    data_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for name, obj in files.items():
        p = data_dir / name
        if p.is_file():
            shutil.copy2(p, p.with_suffix(".json.bak_%s" % stamp))
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n[OK] 已写入 %s（原文件已备份为 *.bak_%s）" % (data_dir, stamp))
    print("     下一步：python ingest_check.py --workspace \"%s\" --ep %s" % (ws, a.ep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
