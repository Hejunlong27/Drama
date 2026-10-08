# -*- coding: utf-8 -*-
"""episode_preflight.py —— 单/多集出片前置体检（零计费）

用法：
    python scripts\\episode_preflight.py 4 5 6
    python scripts\\episode_preflight.py 4 --json
    python scripts\\episode_preflight.py 4 5 6 --plan-refs     # 只打印「应有 character_refs」

核心口径（2026-09-19 从第三/四/五/六集实测归纳）：
  `subject_definitions` 里的 `<Picture N>` 是对**全部 Subject 顺序编号**的 ——
  角色、场景、道具都占号。而 `character_refs` 只提供图片，顺序 = 槽位顺序 = Picture 号顺序。
  ⇒ 只要中间缺一个角色的图，**它后面的场景图就会错位**。
  所以补资产时不能「把场景图追加到末尾」，必须按 Picture 号逐个落位。

资产库口径：资产 md 的「节号 = char 编号」。char_01..09 = 第 1..9 节。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))  # noqa: E402
from _ws import WS  # noqa: E402  ★ 工作区解析（env DRAMA_WORKSPACE > 位置推断 > 报错），见 scripts/_ws.py
VP = WS / "video-pipeline"
DATA = VP / "data"
ASSET_MD = Path(os.environ.get("DRAMA_ASSET_MD") or "")   # 资产提示词 md（可选：缺省时跳过资产核对）

RH_PER_SEG = 180          # 第三集 13 段实测口径（均值 175，留余量）
CNY_PER_RH = 0.0025       # 1 RH币 ≈ ¥0.0025（用户 2026-09-18）；2026-09-19 用户补：1 美元 = 2746 RH币
RH_PER_USD = 2746         # ⇒ 1 RH币 ≈ $0.0003642；用于把美元钱包消耗并回 RH币口径
CHAR_RH = 27              # 人物定妆图单价
SCENE_USD = 0.01          # 场景空镜单价（美元钱包）
MAX_SLOTS = 6             # H3 工作流/AI 应用的 LoadImage 槽位数（node 51/49/50/43/19/23）——硬上限

# 场景关键词 → 资产键（键名按本项目约定，尚未接进 characters.json 的也会列出）
SCENE_KEYS = [
    ("陆家宗祠天井", "scene_02"),
    ("三亚海景五星酒店露天阳台", "scene_03"),
    ("亚特兰蒂斯", "scene_04"),
    ("拘留所", "scene_05"),
    ("沈家老宅", "scene_06"),
    ("钟楼", "scene_07"),
    ("酒楼", "scene_01"),
    ("大包厢", "scene_01"),
]


def load_all():
    shots = json.loads((DATA / "shots.json").read_text(encoding="utf-8"))
    prompts = json.loads((DATA / "h3_prompts.json").read_text(encoding="utf-8"))
    chars = json.loads((DATA / "characters.json").read_text(encoding="utf-8"))
    return shots, prompts, chars


def parse_asset_md():
    if not ASSET_MD.is_file():
        return {}, {}
    text = ASSET_MD.read_text(encoding="utf-8", errors="replace")
    chars, scenes, zone = {}, {}, None
    for line in text.splitlines():
        if line.startswith("# 一、"):
            zone = "char"; continue
        if line.startswith("# 二、"):
            zone = "scene"; continue
        if line.startswith("# 三、"):
            zone = "prop"; continue
        if zone == "char":
            m = re.match(r"##\s*(\d+)\.\s*([^（(｜|]+)", line)
            if m:
                chars[int(m.group(1))] = m.group(2).strip()
        elif zone == "scene":
            m = re.match(r"##\s*场景\s*(\d+)\s*[·:：]\s*([^（(｜|]+)", line)
            if m:
                scenes[int(m.group(1))] = m.group(2).strip()
    return chars, scenes


def on_disk_assets():
    out = {}
    for sub, pat, pre in (("characters", "char_*.png", "char_"), ("scenes", "scene_*.png", "scene_")):
        d = VP / "output" / "assets" / sub
        if not d.is_dir():
            continue
        for f in d.glob(pat):
            m = re.match(re.escape(pre) + r"(\d+)_(.+?)(_r\d+|_preview)?$", f.stem)
            if m:
                out["%s%02d" % (pre, int(m.group(1)))] = m.group(2)
    return out


def sect(prompt: str, name: str) -> str:
    m = re.search(r"\n" + name + r":\n(.*?)(?=\n[a-z_]+:|\Z)", prompt, re.S)
    return m.group(1) if m else ""


def norm(s: str) -> str:
    return re.sub(r"[\s·、（）()【】\[\]]", "", s or "")


def name_to_section(name, char_sections):
    n = norm(name)
    if not n:
        return None
    for sec, nm in char_sections.items():
        if norm(nm) and (norm(nm) in n or n in norm(nm)):
            return sec
    return None


def scene_key_of(text: str):
    for kw, key in SCENE_KEYS:
        if kw in text:
            return key
    return None


def analyze(ep, shots, prompts, chars, char_sections):
    segs = sorted([s for s in shots if s.get("shot_id", "").startswith(ep + "_")],
                  key=lambda s: s["shot_id"])
    available = {k for k in chars if not k.startswith("_")}
    rows, missing_objs = [], {}
    for s in segs:
        sid = s["shot_id"]
        p = prompts.get(sid)
        refs = list(s.get("character_refs") or [])
        if not p or not str(p.get("prompt") or "").strip():
            rows.append({"shot": sid, "refs": refs, "target": None, "issue": "无提示词", "maxP": 0})
            continue
        sd = sect(p["prompt"], "subject_definitions")
        subs = []
        for m in re.finditer(r"<Subject (\d+)> 是 (?:<Picture (\d+)> )?中(?:的)?([^\n]+)", sd):
            subs.append({"subj": int(m.group(1)),
                         "pic": int(m.group(2)) if m.group(2) else None,
                         "text": m.group(3).strip()})
        maxP = max([x["pic"] for x in subs if x["pic"]] or [0])
        target, issues = [], []
        for x in subs:
            pic, txt = x["pic"], x["text"]
            if pic is None:
                continue
            if "形象" in txt:
                nm = re.sub(r"形象.*$", "", txt).strip()
                sec = name_to_section(nm, char_sections)
                key = "char_%02d" % sec if sec else None
                if key and key in available:
                    target.append((pic, key))
                else:
                    target.append((pic, "❌%s%s" % (nm, "(第%s节)" % sec if sec else "(库外)")))
                    missing_objs.setdefault(nm, sec)
                    issues.append("P%d 缺角色图:%s%s" % (pic, nm, "（第%s节）" % sec if sec else "（资产库无此人）"))
            elif txt.startswith("道具"):
                pass                                   # 道具不用图
            else:
                sk = scene_key_of(txt)
                if sk:
                    target.append((pic, sk))
                    if sk not in available:
                        issues.append("P%d 缺场景图:%s（%s 未接入）" % (pic, txt[:16], sk))
                else:
                    issues.append("P%d 未识别场景:%s" % (pic, txt[:16]))
        # 落位检查：target 的 pic 必须连续 1..len(target)
        pics = [t[0] for t in target]
        if pics != list(range(1, len(pics) + 1)):
            issues.append("落位不连续:%s" % pics)
        # ★ 槽位上限：H3 工作流/AI 应用只有 6 个 LoadImage 槽位（node 51/49/50/43/19/23）
        if len(target) > MAX_SLOTS:
            issues.append("★需要 %d 张图，但只有 %d 个槽位（末尾的会被丢掉 ⇒ 必须删掉超出者的 <Picture>）"
                          % (len(target), MAX_SLOTS))
        want = [k for _, k in target]
        rows.append({"shot": sid, "refs": refs, "target": want, "want_pics": pics,
                     "maxP": maxP, "issue": " | ".join(issues) or "OK"})
    return {"ep": ep, "n": len(segs), "rows": rows, "missing": missing_objs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("episodes", nargs="+")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--plan-refs", action="store_true")
    args = ap.parse_args()

    shots, prompts, chars = load_all()
    char_sections, scene_sections = parse_asset_md()
    disk = on_disk_assets()
    available = {k for k in chars if not k.startswith("_")}
    report = [analyze("ep%02d" % int(e), shots, prompts, chars, char_sections) for e in args.episodes]

    if args.json:
        print(json.dumps({"available": sorted(available), "disk": disk, "eps": report},
                         ensure_ascii=False, indent=2))
        return 0

    print("=" * 92)
    print("出片前置体检（零计费）")
    print("  已接入 characters.json : %s" % ", ".join(sorted(available)))
    unwired = {k: v for k, v in sorted(disk.items()) if k not in available}
    print("  已生成但未接入        : %s" % (", ".join("%s(%s)" % (k, v) for k, v in unwired.items()) or "无"))
    print("=" * 92)

    total, all_missing = 0, {}
    for r in report:
        total += r["n"]
        print("\n【%s】%d 段" % (r["ep"], r["n"]))
        print("-" * 92)
        if args.plan_refs:
            for row in r["rows"]:
                print("  %-14s 当前: %s" % (row["shot"], ",".join(row["refs"]) or "—"))
                print("  %-14s 应有: %s" % ("", ",".join(row["target"]) if row["target"] else "—"))
                if row["issue"] != "OK":
                    print("  %-14s 问题: %s" % ("", row["issue"]))
        else:
            print("  %-14s %-34s %-5s %-5s %s" % ("shot", "应有参考图", "maxP", "实发", "问题"))
            for row in r["rows"]:
                print("  %-14s %-34s %-5s %-5s %s"
                      % (row["shot"], ",".join(row["target"])[:34] if row["target"] else "—",
                         row["maxP"] or "—", len(row["refs"]), row["issue"]))
        if r["missing"]:
            all_missing.update(r["missing"])
            print("  ★ 本集缺失角色: %s"
                  % ", ".join("%s%s" % (n, "（第%s节，%s）" % (s, disk.get("char_%02d" % s, "未生成")) if s else "（资产库无此人）")
                              for n, s in sorted(r["missing"].items(), key=lambda x: x[1] or 99)))
        print("  成本估算: %d 段 × %d ≈ %d RH币 ≈ ¥%.2f"
              % (r["n"], RH_PER_SEG, r["n"] * RH_PER_SEG, r["n"] * RH_PER_SEG * CNY_PER_RH))

    print("\n" + "=" * 92)
    print("汇总")
    print("=" * 92)
    print("  段数        : %d" % total)
    print("  视频费用    : %d RH币 ≈ ¥%.2f" % (total * RH_PER_SEG, total * RH_PER_SEG * CNY_PER_RH))
    known = {n: s for n, s in all_missing.items() if s}
    unknown = [n for n, s in all_missing.items() if not s]
    if known:
        print("  需补人物    : %d 个 → %s" % (len(known), ", ".join("第%s节%s" % (s, n) for n, s in sorted(known.items(), key=lambda x: x[1]))))
        print("  人物出图费  : %d × %d RH币 ≈ %d RH币 ≈ ¥%.2f"
              % (len(known), CHAR_RH, len(known) * CHAR_RH, len(known) * CHAR_RH * CNY_PER_RH))
    if unknown:
        print("  资产库没有  : %s  ← 需新写提示词或改为纯文字" % ", ".join(unknown))
    print("  合计（视频+人物）≈ %d RH币 ≈ ¥%.2f"
          % (total * RH_PER_SEG + len(known) * CHAR_RH,
             (total * RH_PER_SEG + len(known) * CHAR_RH) * CNY_PER_RH))
    return 0


if __name__ == "__main__":
    sys.exit(main())
